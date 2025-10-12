import sounddevice as sd
import numpy as np
import time
from collections import deque
import queue

class AudioChannelRouter:
    def __init__(self):
        self.CHUNK = 512
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
        
        self.running = False
        self.stream = None
        
    def db_from_amplitude(self, amplitude):
        """Convert amplitude to dB"""
        if amplitude < 1e-10:
            return -100
        return 20 * np.log10(amplitude)
    
    def get_channel_levels(self, audio_data):
        """Calculate RMS level for each channel"""
        levels = []
        for i in range(self.INPUT_CHANNELS):
            channel_data = audio_data[:, i]
            rms = np.sqrt(np.mean(channel_data**2))
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
    
    def audio_callback(self, indata, outdata, frames, time_info, status):
        """Audio callback function"""
        if status:
            print(f"Status: {status}")
        
        # Get levels for all channels
        levels = self.get_channel_levels(indata)
        
        # Select active channel
        active_ch = self.select_active_channel(levels)
        
        # Initialize output with silence
        outdata.fill(0)
        
        if active_ch is not None:
            # Extract active channel data and route to both stereo channels
            channel_data = indata[:, active_ch]
            outdata[:, 0] = channel_data  # Left
            outdata[:, 1] = channel_data  # Right
            
            # Print status (limit output to first 5 channels for readability)
            print(f"\rActive: Ch{active_ch + 1} ({levels[active_ch]:>5.1f} dB) | " + 
                  " ".join([f"Ch{i+1}:{lvl:>4.0f}" for i, lvl in enumerate(levels)[:5]]), 
                  end="", flush=True)
        else:
            print("\r" + "No active channel" + " " * 60, end="", flush=True)
    
    def list_devices(self):
        """List all audio devices"""
        print("\n" + "="*70)
        print("AVAILABLE AUDIO DEVICES")
        print("="*70)
        devices = sd.query_devices()
        
        for i, device in enumerate(devices):
            print(f"\nDevice {i}: {device['name']}")
            print(f"  Input Channels:  {device['max_input_channels']}")
            print(f"  Output Channels: {device['max_output_channels']}")
            print(f"  Default Sample Rate: {device['default_samplerate']} Hz")
            
            # Highlight devices that could work
            if device['max_input_channels'] >= 10:
                print(f"  ✓ Can be used as INPUT (has 10+ channels)")
            if device['max_output_channels'] >= 2:
                print(f"  ✓ Can be used as OUTPUT (has stereo)")
        
        print("\n" + "="*70)
    
    def start(self, input_device_idx, output_device_idx):
        """Start the audio router"""
        if self.running:
            print("Already running!")
            return
        
        try:
            print(f"\n{'='*70}")
            print("STARTING AUDIO ROUTER")
            print("="*70)
            print(f"Input Device:  {input_device_idx} - {sd.query_devices(input_device_idx)['name']}")
            print(f"Output Device: {output_device_idx} - {sd.query_devices(output_device_idx)['name']}")
            print(f"Sample Rate:   {self.RATE} Hz")
            print(f"Threshold:     {self.threshold_db} dB")
            print(f"Priority Channels: {self.priority_channels}")
            print(f"Channel 3 Exclusive Mode: {self.channel_3_exclusive}")
            print(f"Hold Time:     {self.channel_hold_time} seconds")
            print("="*70)
            
            self.running = True
            
            # Create and start stream
            self.stream = sd.Stream(
                samplerate=self.RATE,
                blocksize=self.CHUNK,
                device=(input_device_idx, output_device_idx),
                channels=(self.INPUT_CHANNELS, self.OUTPUT_CHANNELS),
                dtype=np.float32,
                callback=self.audio_callback
            )
            
            self.stream.start()
            print("\n✓ Router is running! Press Ctrl+C to stop.\n")
            
            # Keep running
            while self.running:
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\n\n" + "="*70)
            print("STOPPING...")
            print("="*70)
        except Exception as e:
            print(f"\n\nERROR: {e}")
            print("\nTroubleshooting:")
            print("- Make sure the Zoom H8 is connected and recognized")
            print("- Check that device numbers are correct")
            print("- Try different sample rates if 48000 Hz doesn't work")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the audio router"""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        print("\n✓ Router stopped.\n")


def main():
    print("\n" + "="*70)
    print("ZOOM H8 AUDIO CHANNEL ROUTER")
    print("="*70)
    
    router = AudioChannelRouter()
    
    # List available devices
    router.list_devices()
    
    # Get device selection
    print("\nDEVICE SELECTION")
    print("-" * 70)
    input_device = int(input("Enter INPUT device number (Zoom H8): "))
    output_device = int(input("Enter OUTPUT device number (Virtual/Recording): "))
    
    # Optional: Configure settings
    print("\n" + "="*70)
    print("CONFIGURATION (Optional)")
    print("="*70)
    configure = input("Would you like to configure settings? (y/n, default=n): ").lower()
    
    if configure == 'y':
        print("\nCurrent settings:")
        print(f"  Threshold: {router.threshold_db} dB")
        print(f"  Priority channels: {router.priority_channels}")
        print(f"  Channel 3 exclusive: {router.channel_3_exclusive}")
        print(f"  Hold time: {router.channel_hold_time} seconds")
        print()
        
        threshold = input(f"New threshold in dB (press Enter to keep {router.threshold_db}): ")
        if threshold:
            router.threshold_db = float(threshold)
        
        priority = input(f"Priority channels, comma-separated (press Enter to keep {','.join(map(str, router.priority_channels))}): ")
        if priority:
            router.priority_channels = [int(x.strip()) for x in priority.split(',')]
        
        exclusive = input(f"Channel 3 exclusive mode? (y/n, press Enter to keep {'y' if router.channel_3_exclusive else 'n'}): ")
        if exclusive:
            router.channel_3_exclusive = exclusive.lower() == 'y'
        
        hold = input(f"Hold time in seconds (press Enter to keep {router.channel_hold_time}): ")
        if hold:
            router.channel_hold_time = float(hold)
    
    # Start routing
    router.start(input_device, output_device)


if __name__ == "__main__":
    main()
    