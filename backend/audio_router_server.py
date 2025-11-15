from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sounddevice as sd
import numpy as np
import time
from collections import deque
import threading
import wave
from datetime import datetime
import json

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

class AudioChannelRouter:
    def __init__(self):
        self.CHUNK = 1024
        self.RATE = 48000
        self.INPUT_CHANNELS = 10
        self.OUTPUT_CHANNELS = 2
        
        # Configuration
        self.threshold_db = -40
        self.input_gain_db = 0
        self.output_gain_db = 0
        self.priority_channels = [1, 3]
        self.channel_3_exclusive = True
        self.active_channel = None
        self.channel_hold_time = 0.5
        self.last_switch_time = time.time()
        
        # Audio level history - will be resized based on actual device
        self.level_history = {}
        self.current_levels = []
        
        self.running = False
        self.input_stream = None
        self.output_stream = None
        
        # Devices
        self.input_device = None
        self.output_device = None
        
        # WAV recording
        self.record_to_file = False
        self.wav_file = None
        self.recorded_frames = []
        self.recording_filename = None
        self.recording_start_time = None
        
    def db_to_linear(self, db):
        return 10 ** (db / 20)
    
    def db_from_amplitude(self, amplitude):
        if amplitude < 1e-10:
            return -100
        return 20 * np.log10(amplitude)
    
    def get_channel_levels(self, audio_data):
        levels = []
        gain_linear = self.db_to_linear(self.input_gain_db)
        
        for i in range(self.INPUT_CHANNELS):
            channel_data = audio_data[:, i] * gain_linear
            
            # Use peak level instead of RMS to match ZOOM H8 hardware meters
            # Peak level shows 0 dB when signal reaches full scale (amplitude = 1.0)
            peak = np.max(np.abs(channel_data))
            db = self.db_from_amplitude(peak)
            
            self.level_history[i].append(db)
            smoothed_db = np.mean(list(self.level_history[i]))
            levels.append(smoothed_db)
        
        self.current_levels = levels
        return levels
    
    def select_active_channel(self, levels):
        current_time = time.time()
        
        if self.active_channel is not None:
            if current_time - self.last_switch_time < self.channel_hold_time:
                if levels[self.active_channel] > self.threshold_db:
                    return self.active_channel
        
        if self.channel_3_exclusive and levels[2] > self.threshold_db:
            if levels[0] > self.threshold_db and levels[0] > levels[2]:
                new_channel = 0
            else:
                new_channel = 2
            
            if new_channel != self.active_channel:
                self.active_channel = new_channel
                self.last_switch_time = current_time
            return self.active_channel
        
        priority_indices = [ch - 1 for ch in self.priority_channels]
        for idx in priority_indices:
            if levels[idx] > self.threshold_db:
                if idx != self.active_channel:
                    self.active_channel = idx
                    self.last_switch_time = current_time
                return self.active_channel
        
        active_channels = [(i, level) for i, level in enumerate(levels) 
                          if level > self.threshold_db]
        
        if active_channels:
            new_channel = max(active_channels, key=lambda x: x[1])[0]
            if new_channel != self.active_channel:
                self.active_channel = new_channel
                self.last_switch_time = current_time
            return self.active_channel
        
        self.active_channel = None
        return None
    
    def process_audio_blocking(self):
        try:
            print(f"Opening audio streams...")
            print(f"  Input: Device {self.input_device}, {self.INPUT_CHANNELS} channels")
            print(f"  Output: Device {self.output_device}, {self.OUTPUT_CHANNELS} channels")
            
            input_stream = sd.InputStream(
                device=self.input_device,
                channels=self.INPUT_CHANNELS,
                samplerate=self.RATE,
                blocksize=self.CHUNK,
                dtype=np.float32
            )
            
            output_stream = sd.OutputStream(
                device=self.output_device,
                channels=self.OUTPUT_CHANNELS,
                samplerate=self.RATE,
                blocksize=self.CHUNK,
                dtype=np.float32
            )
            
            input_stream.start()
            output_stream.start()
            
            print("✓ Audio streams started successfully")
            
            frame_count = 0
            last_print_time = time.time()
            
            while self.running:
                try:
                    indata, overflowed = input_stream.read(self.CHUNK)
                    
                    if overflowed:
                        print("!", end="", flush=True)
                    
                    # Debug: Print audio stats every 2 seconds
                    current_time = time.time()
                    if current_time - last_print_time > 2.0:
                        max_input = np.max(np.abs(indata))
                        print(f"\n[DEBUG] Frame {frame_count}: Max input level: {max_input:.6f}")
                        last_print_time = current_time
                    
                    input_gain_linear = self.db_to_linear(self.input_gain_db)
                    indata_gained = indata * input_gain_linear
                    
                    levels = self.get_channel_levels(indata)
                    active_ch = self.select_active_channel(levels)
                    
                    # Debug: Print levels
                    if current_time - last_print_time < 0.1:  # Print after level calc
                        print(f"[DEBUG] Levels (dB): {[f'{l:.1f}' for l in levels[:5]]}")
                        print(f"[DEBUG] Active channel: {active_ch}")
                    
                    outdata = np.zeros((self.CHUNK, self.OUTPUT_CHANNELS), dtype=np.float32)
                    
                    if active_ch is not None:
                        output_gain_linear = self.db_to_linear(self.output_gain_db)
                        channel_data = indata_gained[:, active_ch] * output_gain_linear
                        channel_data = np.clip(channel_data, -1.0, 1.0)
                        
                        outdata[:, 0] = channel_data
                        outdata[:, 1] = channel_data
                    
                    # Debug: Print output level
                    max_output = np.max(np.abs(outdata))
                    if frame_count % 50 == 0:
                        print(f"[DEBUG] Output level: {max_output:.6f}")
                    
                    # Emit levels via WebSocket every 10 frames (reduce load)
                    frame_count += 1
                    if frame_count % 10 == 0:
                        socketio.emit('levels', {
                            'levels': self.current_levels,
                            'active_channel': active_ch + 1 if active_ch is not None else None
                        })
                    
                    if self.record_to_file:
                        audio_int16 = (outdata * 32767).astype(np.int16)
                        self.recorded_frames.append(audio_int16.tobytes())
                    
                    output_stream.write(outdata)
                    
                except Exception as e:
                    print(f"\nError in audio loop: {e}")
                    import traceback
                    traceback.print_exc()
                    break
            
            print("\nStopping streams...")
            input_stream.stop()
            output_stream.stop()
            input_stream.close()
            output_stream.close()
            print("✓ Streams stopped")
            
        except Exception as e:
            print(f"\n✗ Error in audio processing: {e}")
            import traceback
            traceback.print_exc()
            self.running = False
            socketio.emit('error', {'message': str(e)})
    
    def start_routing(self, input_device, output_device):
        if self.running:
            return False
        
        # Get actual device info to determine channel count
        try:
            device_info = sd.query_devices(input_device)
            actual_channels = int(device_info['max_input_channels'])
            
            # Update INPUT_CHANNELS based on actual device
            self.INPUT_CHANNELS = min(actual_channels, 10)  # Use up to 10 channels
            
            # Reinitialize level history for actual channel count
            self.level_history = {i: deque(maxlen=5) for i in range(self.INPUT_CHANNELS)}
            self.current_levels = [0] * self.INPUT_CHANNELS
            
            print(f"Using {self.INPUT_CHANNELS} input channels from device")
        except Exception as e:
            print(f"Warning: Could not determine channel count: {e}")
            self.INPUT_CHANNELS = 10  # Fall back to 10
            self.level_history = {i: deque(maxlen=5) for i in range(self.INPUT_CHANNELS)}
            self.current_levels = [0] * self.INPUT_CHANNELS
        
        self.input_device = input_device
        self.output_device = output_device
        self.running = True
        
        # Start audio processing in separate thread
        self.audio_thread = threading.Thread(target=self.process_audio_blocking)
        self.audio_thread.start()
        
        return True
    
    def stop_routing(self):
        self.running = False
        if hasattr(self, 'audio_thread'):
            self.audio_thread.join(timeout=2)
    
    def start_recording(self, filename=None):
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{timestamp}.wav"
        
        self.wav_file = wave.open(filename, 'wb')
        self.wav_file.setnchannels(self.OUTPUT_CHANNELS)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(self.RATE)
        self.record_to_file = True
        self.recorded_frames = []
        self.recording_filename = filename
        self.recording_start_time = time.time()
        
        return filename
    
    def stop_recording(self):
        if self.wav_file:
            for frame in self.recorded_frames:
                self.wav_file.writeframes(frame)
            self.wav_file.close()
            self.wav_file = None
            self.record_to_file = False
            filename = self.recording_filename
            self.recording_filename = None
            self.recording_start_time = None
            return filename
        return None
    
    def get_config(self):
        return {
            'threshold_db': self.threshold_db,
            'input_gain_db': self.input_gain_db,
            'output_gain_db': self.output_gain_db,
            'priority_channels': self.priority_channels,
            'channel_3_exclusive': self.channel_3_exclusive,
            'channel_hold_time': self.channel_hold_time,
            'input_device': self.input_device,
            'output_device': self.output_device
        }
    
    def update_config(self, config):
        if 'threshold_db' in config:
            self.threshold_db = float(config['threshold_db'])
        if 'input_gain_db' in config:
            self.input_gain_db = float(config['input_gain_db'])
        if 'output_gain_db' in config:
            self.output_gain_db = float(config['output_gain_db'])
        if 'priority_channels' in config:
            self.priority_channels = config['priority_channels']
        if 'channel_3_exclusive' in config:
            self.channel_3_exclusive = config['channel_3_exclusive']
        if 'channel_hold_time' in config:
            self.channel_hold_time = float(config['channel_hold_time'])

# Global router instance
router = AudioChannelRouter()

# API Routes

@app.route('/api/devices', methods=['GET'])
def get_devices():
    try:
        # Force refresh device list
        sd._terminate()
        sd._initialize()
        
        devices = sd.query_devices()
        device_list = []
        
        for i, device in enumerate(devices):
            device_list.append({
                'index': i,
                'name': device['name'],
                'input_channels': int(device['max_input_channels']),
                'output_channels': int(device['max_output_channels']),
                'sample_rate': float(device['default_samplerate'])
            })
        
        return jsonify({'devices': device_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/start', methods=['POST'])
def start_router():
    try:
        data = request.json
        input_device = data.get('input_device')
        output_device = data.get('output_device')
        
        if input_device is None or output_device is None:
            return jsonify({'error': 'Input and output devices required'}), 400
        
        success = router.start_routing(input_device, output_device)
        
        if success:
            return jsonify({'status': 'started'})
        else:
            return jsonify({'error': 'Router already running'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop', methods=['POST'])
def stop_router():
    try:
        router.stop_routing()
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    try:
        return jsonify({
            'running': router.running,
            'active_channel': router.active_channel + 1 if router.active_channel is not None else None,
            'recording': router.record_to_file,
            'recording_filename': router.recording_filename,
            'recording_duration': time.time() - router.recording_start_time if router.recording_start_time else 0
        })
    except Exception as e:
        print(f"Error in status endpoint: {e}")
        return jsonify({
            'running': False,
            'active_channel': None,
            'recording': False,
            'recording_filename': None,
            'recording_duration': 0
        })

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(router.get_config())

@app.route('/api/config', methods=['POST'])
def update_config():
    try:
        data = request.json
        router.update_config(data)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record/start', methods=['POST'])
def start_recording():
    try:
        data = request.json
        filename = data.get('filename')
        filename = router.start_recording(filename)
        return jsonify({'status': 'recording', 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record/stop', methods=['POST'])
def stop_recording():
    try:
        filename = router.stop_recording()
        return jsonify({'status': 'stopped', 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/presets', methods=['GET'])
def get_presets():
    try:
        with open('presets.json', 'r') as f:
            presets = json.load(f)
        return jsonify({'presets': presets})
    except FileNotFoundError:
        return jsonify({'presets': []})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/presets', methods=['POST'])
def save_preset():
    try:
        data = request.json
        name = data.get('name')
        
        try:
            with open('presets.json', 'r') as f:
                presets = json.load(f)
        except FileNotFoundError:
            presets = []
        
        preset = {
            'name': name,
            'config': router.get_config()
        }
        
        presets.append(preset)
        
        with open('presets.json', 'w') as f:
            json.dump(presets, f, indent=2)
        
        return jsonify({'status': 'saved', 'preset': preset})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-audio', methods=['GET'])
def test_audio():
    """Test endpoint to check if audio is flowing"""
    try:
        return jsonify({
            'running': router.running,
            'current_levels': router.current_levels,
            'active_channel': router.active_channel,
            'input_device': router.input_device,
            'output_device': router.output_device,
            'input_channels': router.INPUT_CHANNELS
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting Audio Router Server...")
    print("Open http://localhost:5001 in your browser")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
