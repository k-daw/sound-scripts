import pyaudio
import numpy as np
import threading
import time
from collections import deque

class AudioChannelRouter:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.CHUNK = 512
        self.FORMAT = pyaudio.paInt16
        self.RATE = 48000
        self.INPUT_CHANNELS = 10
        self.OUTPUT_CHANNELS = 2
        
        # Configuration
        self.threshold_db = -40  # Minimum dB to consider channel active
        self.priority_channels = [1, 3]  # Channels with priority (1-indexed)
        self.channel_3_exclusive = True  # When channel 3 active, only allow channel 1
        self.active_channel = None
        self.channel_hold_time = 0.5  # Seconds to hold a channel before switching
        self.last_switch_time = time.time()
        
        # Audio level history for smoothing
        self.level_history = {i: deque(maxlen=5) for i in range(self.INPUT_CHANNELS)}
        
        # Output routing (which input channel goes to which output channel)
        self.output_routing = {
            'left': None,   # Will be set to active channel
            'right': None   # Can duplicate or use different channel
        }
        
        self.running = False
        self.input_stream = None
        self.output_stream = None
        
    def db_from_amplitude(self, amplitude):
        """Convert amplitude to dB"""
        if amplitude < 1e-10:
            return -100
        return 20 * np.log10(amplitude)
    
    def get_channel_levels(self, audio_data):
        """Calculate RMS level for each channel"""
        levels = []
        for i in range(self.INPUT_CHANNELS):
            channel_data = audio_data[i::self.INPUT_CHANNELS]
            rms = np.sqrt(np.mean(channel_data.astype(np.float32)**2))
            db = self.db_from_amplitude(rms)
            
            # Add to history and get smoothed value
            self.level_history[i].append(db)
            smoothed_db = np.mean(list(self.level_history[i]))
            levels.append(smoothed_db)
        
        return levels
    
    def select_active_channel(self, levels):
        """Select which channel should be active based on logic"""
        current_time = time.time()
        
        # Check if we should hold current channel
        if self.active_channel is not None:
            if current_time - self.last_switch_time < self.channel_hold_time:
                # Check if current channel is still above threshold
                if levels[self.active_channel] > self.threshold_db:
                    return self.active_channel
        
        # Special logic: If channel 3 (index 2) is active
        if self.channel_3_exclusive and levels[2] > self.threshold_db:
            # Only allow channel 1 (index 0) or channel 3 (index 2)
            if levels[0] > self.threshold_db and levels[0] > levels[2]:
                new_channel = 0
            else:
                new_channel = 2
            
            if new_channel != self.active_channel:
                self.active_channel = new_channel
                self.last_switch_time = current_time
            return self.active_channel
        
        # Priority channels first
        priority_indices = [ch - 1 for ch in self.priority_channels]
        for idx in priority_indices:
            if levels[idx] > self.threshold_db:
                if idx != self.active_channel:
                    self.active_channel = idx
                    self.last_switch_time = current_time
                return self.active_channel
        
        # Find channel with highest level above threshold
        active_channels = [(i, level) for i, level in enumerate(levels) 
                          if level > self.threshold_db]
        
        if active_channels:
            new_channel = max(active_channels, key=lambda x: x[1])[0]
            if new_channel != self.active_channel:
                self.active_channel = new_channel
                self.last_switch_time = current_time
            return self.active_channel
        
        # No active channels
        self.active_channel = None
        return None
    
    def process_audio(self, in_data, frame_count, time_info, status):
        """Audio callback function"""
        # Convert bytes to numpy array
        audio_data = np.frombuffer(in_data, dtype=np.int16)
        
        # Get levels for all channels
        levels = self.get_channel_levels(audio_data)
        
        # Select active channel
        active_ch = self.select_active_channel(levels)
        
        # Create output audio (stereo)
        output_data = np.zeros(frame_count * self.OUTPUT_CHANNELS, dtype=np.int16)
        
        if active_ch is not None:
            # Extract active channel data
            channel_data = audio_data[active_ch::self.INPUT_CHANNELS]
            
            # Route to both left and right (stereo)
            output_data[0::2] = channel_data  # Left
            output_data[1::2] = channel_data  # Right
            
            # Print status
            print(f"\rActive: Ch{active_ch + 1} ({levels[active_ch]:.1f} dB)  " + 
                  " ".join([f"Ch{i+1}:{lvl:.0f}" for i, lvl in enumerate(levels)[:5]]), 
                  end="", flush=True)
        else:
            print("\rNo active channel" + " " * 50, end="", flush=True)
        
        return (output_data.tobytes(), pyaudio.paContinue)
    
    def list_devices(self):
        """List all audio devices"""
        print("\n=== Available Audio Devices ===")
        for i in range(self.p.get_device_count()):
            info = self.p.get_device_info_by_index(i)
            print(f"\nDevice {i}: {info['name']}")
            print(f"  Max Input Channels: {info['maxInputChannels']}")
            print(f"  Max Output Channels: {info['maxOutputChannels']}")
            print(f"  Default Sample Rate: {info['defaultSampleRate']}")
    
    def start(self, input_device_idx, output_device_idx):
        """Start the audio router"""
        if self.running:
            print("Already running!")
            return
        
        try:
            print(f"\nStarting Audio Router...")
            print(f"Input Device: {input_device_idx}")
            print(f"Output Device: {output_device_idx}")
            print(f"Threshold: {self.threshold_db} dB")
            print(f"Priority Channels: {self.priority_channels}")
            print(f"Channel 3 Exclusive Mode: {self.channel_3_exclusive}")
            
            self.running = True
            
            # Open streams
            self.input_stream = self.p.open(
                format=self.FORMAT,
                channels=self.INPUT_CHANNELS,
                rate=self.RATE,
                input=True,
                output=True,  # Duplex mode
                input_device_index=input_device_idx,
                output_device_index=output_device_idx,
                frames_per_buffer=self.CHUNK,
                stream_callback=self.process_audio,
                output_channels=self.OUTPUT_CHANNELS
            )
            
            self.input_stream.start_stream()
            print("\n\nRouter is running! Press Ctrl+C to stop.\n")
            
            # Keep running
            while self.running and self.input_stream.is_active():
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\n\nStopping...")
        except Exception as e:
            print(f"\nError: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the audio router"""
        self.running = False
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        print("\nRouter stopped.")
    
    def __del__(self):
        self.p.terminate()


def main():
    router = AudioChannelRouter()
    
    # List available devices
    router.list_devices()
    
    print("\n" + "="*50)
    input_device = int(input("\nEnter INPUT device number (Zoom H8): "))
    output_device = int(input("Enter OUTPUT device number (Virtual/Recording device): "))
    
    # Optional: Configure settings
    print("\n=== Configuration ===")
    configure = input("Configure settings? (y/n, default=n): ").lower()
    
    if configure == 'y':
        router.threshold_db = float(input(f"Threshold in dB (default={router.threshold_db}): ") or router.threshold_db)
        priority = input(f"Priority channels comma-separated (default={','.join(map(str, router.priority_channels))}): ")
        if priority:
            router.priority_channels = [int(x.strip()) for x in priority.split(',')]
        
        exclusive = input(f"Channel 3 exclusive mode? (y/n, default={'y' if router.channel_3_exclusive else 'n'}): ")
        router.channel_3_exclusive = exclusive.lower() == 'y'
    
    # Start routing
    router.start(input_device, output_device)


if __name__ == "__main__":
    main()
