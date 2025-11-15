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
import copy

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
        
        # Routing matrix: routing_matrix[input_channel][output_channel] = enabled (bool)
        # This allows multiple input channels to be routed to multiple output channels simultaneously
        self.routing_matrix = {}
        self.use_routing_matrix = False  # Toggle between matrix mode and active channel mode
        self._routing_matrix_lock = threading.Lock()  # Thread safety for routing matrix updates
        
        # Audio level history - will be resized based on actual device
        self.level_history = {}
        self.current_levels = []
        
        # Debug mode
        self.debug_mode = False
        
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
        input_stream = None
        output_stream = None
        try:
            print(f"Opening audio streams...")
            print(f"  Input: Device {self.input_device}, {self.INPUT_CHANNELS} channels")
            print(f"  Output: Device {self.output_device}, {self.OUTPUT_CHANNELS} channels")
            
            try:
                input_stream = sd.InputStream(
                    device=self.input_device,
                    channels=self.INPUT_CHANNELS,
                    samplerate=self.RATE,
                    blocksize=self.CHUNK,
                    dtype=np.float32
                )
            except Exception as e:
                error_msg = f"Failed to open input stream: {e}"
                print(f"✗ {error_msg}")
                socketio.emit('error', {'message': error_msg})
                self.running = False
                return
            
            try:
                output_stream = sd.OutputStream(
                    device=self.output_device,
                    channels=self.OUTPUT_CHANNELS,
                    samplerate=self.RATE,
                    blocksize=self.CHUNK,
                    dtype=np.float32
                )
            except Exception as e:
                error_msg = f"Failed to open output stream: {e}"
                print(f"✗ {error_msg}")
                socketio.emit('error', {'message': error_msg})
                if input_stream:
                    input_stream.close()
                self.running = False
                return
            
            try:
                input_stream.start()
                output_stream.start()
                print("✓ Audio streams started successfully")
            except Exception as e:
                error_msg = f"Failed to start audio streams: {e}"
                print(f"✗ {error_msg}")
                socketio.emit('error', {'message': error_msg})
                if input_stream:
                    input_stream.close()
                if output_stream:
                    output_stream.close()
                self.running = False
                return
            
            frame_count = 0
            last_print_time = time.time()
            
            while self.running:
                try:
                    indata, overflowed = input_stream.read(self.CHUNK)
                    
                    if overflowed:
                        if self.debug_mode:
                            print("!", end="", flush=True)
                    
                    # Debug: Print audio stats every 2 seconds
                    current_time = time.time()
                    if self.debug_mode and current_time - last_print_time > 2.0:
                        max_input = np.max(np.abs(indata))
                        print(f"\n[DEBUG] Frame {frame_count}: Max input level: {max_input:.6f}")
                        last_print_time = current_time
                    
                    input_gain_linear = self.db_to_linear(self.input_gain_db)
                    indata_gained = indata * input_gain_linear
                    
                    levels = self.get_channel_levels(indata)
                    active_ch = self.select_active_channel(levels)
                    
                    # Debug: Print levels
                    if self.debug_mode and current_time - last_print_time < 0.1:
                        print(f"[DEBUG] Levels (dB): {[f'{l:.1f}' for l in levels[:5]]}")
                        print(f"[DEBUG] Active channel: {active_ch}")
                    
                    outdata = np.zeros((self.CHUNK, self.OUTPUT_CHANNELS), dtype=np.float32)
                    
                    output_gain_linear = self.db_to_linear(self.output_gain_db)
                    
                    # Thread-safe copy of routing matrix for this audio block
                    with self._routing_matrix_lock:
                        use_matrix = self.use_routing_matrix
                        if use_matrix:
                            # Make a shallow copy of the routing matrix structure
                            routing_matrix_copy = copy.deepcopy(self.routing_matrix)
                        else:
                            routing_matrix_copy = None
                    
                    if use_matrix:
                        # Use routing matrix mode: route multiple input channels to multiple output channels
                        # Count active routes per output channel for normalization
                        active_routes_per_output = np.zeros(self.OUTPUT_CHANNELS, dtype=np.int32)
                        
                        # First pass: count active routes and accumulate signals
                        for input_ch in range(self.INPUT_CHANNELS):
                            if input_ch in routing_matrix_copy:
                                for output_ch in range(self.OUTPUT_CHANNELS):
                                    if output_ch in routing_matrix_copy[input_ch] and routing_matrix_copy[input_ch][output_ch]:
                                        # Route this input channel to this output channel
                                        channel_data = indata_gained[:, input_ch] * output_gain_linear
                                        # Mix with existing output (sum signals)
                                        outdata[:, output_ch] += channel_data
                                        active_routes_per_output[output_ch] += 1
                        
                        # Normalize outputs that have multiple inputs to prevent clipping
                        # Only normalize if more than 1 input is routed to an output
                        for output_ch in range(self.OUTPUT_CHANNELS):
                            if active_routes_per_output[output_ch] > 1:
                                # Normalize by number of active routes to prevent clipping
                                outdata[:, output_ch] /= active_routes_per_output[output_ch]
                    else:
                        # Use active channel mode (original behavior)
                        if active_ch is not None:
                            channel_data = indata_gained[:, active_ch] * output_gain_linear
                            channel_data = np.clip(channel_data, -1.0, 1.0)
                            
                            outdata[:, 0] = channel_data
                            outdata[:, 1] = channel_data
                    
                    # Final safety clip to prevent any overflow
                    outdata = np.clip(outdata, -1.0, 1.0)
                    
                    # Debug: Print output level
                    if self.debug_mode and frame_count % 50 == 0:
                        max_output = np.max(np.abs(outdata))
                        print(f"[DEBUG] Output level: {max_output:.6f}")
                    
                    # Emit levels via WebSocket every 10 frames (reduce load)
                    frame_count += 1
                    if frame_count % 10 == 0:
                        # Convert numpy types to Python types for JSON serialization
                        levels_list = [float(level) for level in self.current_levels]
                        socketio.emit('levels', {
                            'levels': levels_list,
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
                    # Emit error to frontend
                    socketio.emit('error', {'message': f'Audio processing error: {str(e)}'})
                    # Try to recover - wait a bit and continue
                    time.sleep(0.1)
                    # If error persists, break the loop
                    if not self.running:
                        break
            
            print("\nStopping streams...")
            try:
                if input_stream:
                    input_stream.stop()
                    input_stream.close()
                if output_stream:
                    output_stream.stop()
                    output_stream.close()
                print("✓ Streams stopped")
            except Exception as e:
                print(f"Warning: Error stopping streams: {e}")
            
        except Exception as e:
            print(f"\n✗ Error in audio processing: {e}")
            import traceback
            traceback.print_exc()
            self.running = False
            socketio.emit('error', {'message': str(e)})
            # Ensure streams are closed even on error
            try:
                if input_stream:
                    input_stream.stop()
                    input_stream.close()
                if output_stream:
                    output_stream.stop()
                    output_stream.close()
            except:
                pass
    
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
            
            # Get output device info to determine output channel count
            output_device_info = sd.query_devices(output_device)
            actual_output_channels = int(output_device_info['max_output_channels'])
            self.OUTPUT_CHANNELS = min(actual_output_channels, 8)  # Support up to 8 output channels
            
            # Initialize/update routing matrix for current channel counts
            # Ensure all input channels have entries, and clean up any entries beyond current counts
            with self._routing_matrix_lock:
                new_routing_matrix = {}
                for i in range(self.INPUT_CHANNELS):
                    if i in self.routing_matrix:
                        # Keep existing routing for this input channel, but filter output channels
                        new_routing_matrix[i] = {
                            out_ch: self.routing_matrix[i].get(out_ch, False)
                            for out_ch in range(self.OUTPUT_CHANNELS)
                        }
                    else:
                        new_routing_matrix[i] = {}
                self.routing_matrix = new_routing_matrix
            
            print(f"Using {self.INPUT_CHANNELS} input channels and {self.OUTPUT_CHANNELS} output channels from device")
        except Exception as e:
            print(f"Warning: Could not determine channel count: {e}")
            self.INPUT_CHANNELS = 10  # Fall back to 10
            self.OUTPUT_CHANNELS = 2  # Fall back to stereo
            self.level_history = {i: deque(maxlen=5) for i in range(self.INPUT_CHANNELS)}
            self.current_levels = [0] * self.INPUT_CHANNELS
            # Initialize routing matrix for fallback channel counts
            with self._routing_matrix_lock:
                new_routing_matrix = {}
                for i in range(self.INPUT_CHANNELS):
                    if i in self.routing_matrix:
                        new_routing_matrix[i] = {
                            out_ch: self.routing_matrix[i].get(out_ch, False)
                            for out_ch in range(self.OUTPUT_CHANNELS)
                        }
                    else:
                        new_routing_matrix[i] = {}
                self.routing_matrix = new_routing_matrix
        
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
            if self.audio_thread.is_alive():
                print("Warning: Audio thread did not stop within timeout")
    
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
        with self._routing_matrix_lock:
            routing_matrix_copy = copy.deepcopy(self.routing_matrix)
            use_routing_matrix = self.use_routing_matrix
        return {
            'threshold_db': self.threshold_db,
            'input_gain_db': self.input_gain_db,
            'output_gain_db': self.output_gain_db,
            'priority_channels': self.priority_channels,
            'channel_3_exclusive': self.channel_3_exclusive,
            'channel_hold_time': self.channel_hold_time,
            'input_device': self.input_device,
            'output_device': self.output_device,
            'use_routing_matrix': use_routing_matrix,
            'routing_matrix': routing_matrix_copy,
            'input_channels': self.INPUT_CHANNELS,
            'output_channels': self.OUTPUT_CHANNELS,
            'debug_mode': self.debug_mode
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
        if 'use_routing_matrix' in config:
            with self._routing_matrix_lock:
                self.use_routing_matrix = config['use_routing_matrix']
        if 'routing_matrix' in config:
            # Update routing matrix with thread safety and validation
            with self._routing_matrix_lock:
                for input_ch, output_channels in config['routing_matrix'].items():
                    try:
                        input_ch = int(input_ch)
                        # Validate input channel is within range
                        if input_ch < 0 or input_ch >= self.INPUT_CHANNELS:
                            print(f"Warning: Input channel {input_ch} out of range (0-{self.INPUT_CHANNELS-1}), skipping")
                            continue
                        if input_ch not in self.routing_matrix:
                            self.routing_matrix[input_ch] = {}
                        for output_ch, enabled in output_channels.items():
                            try:
                                output_ch = int(output_ch)
                                # Validate output channel is within range
                                if output_ch < 0 or output_ch >= self.OUTPUT_CHANNELS:
                                    print(f"Warning: Output channel {output_ch} out of range (0-{self.OUTPUT_CHANNELS-1}), skipping")
                                    continue
                                self.routing_matrix[input_ch][output_ch] = bool(enabled)
                            except (ValueError, TypeError) as e:
                                print(f"Warning: Invalid output channel value: {output_ch}, skipping")
                    except (ValueError, TypeError) as e:
                        print(f"Warning: Invalid input channel value: {input_ch}, skipping")
        if 'debug_mode' in config:
            self.debug_mode = bool(config['debug_mode'])

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

@app.route('/api/routing-matrix', methods=['GET'])
def get_routing_matrix():
    """Get the current routing matrix configuration"""
    try:
        with router._routing_matrix_lock:
            routing_matrix_copy = copy.deepcopy(router.routing_matrix)
            use_routing_matrix = router.use_routing_matrix
        return jsonify({
            'use_routing_matrix': use_routing_matrix,
            'routing_matrix': routing_matrix_copy,
            'input_channels': router.INPUT_CHANNELS,
            'output_channels': router.OUTPUT_CHANNELS
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/routing-matrix', methods=['POST'])
def update_routing_matrix():
    """Update the routing matrix configuration"""
    try:
        data = request.json
        with router._routing_matrix_lock:
            if 'use_routing_matrix' in data:
                router.use_routing_matrix = bool(data['use_routing_matrix'])
            if 'routing_matrix' in data:
                # Update routing matrix with validation
                for input_ch, output_channels in data['routing_matrix'].items():
                    try:
                        input_ch = int(input_ch)
                        # Validate input channel is within range
                        if input_ch < 0 or input_ch >= router.INPUT_CHANNELS:
                            continue
                        if input_ch not in router.routing_matrix:
                            router.routing_matrix[input_ch] = {}
                        for output_ch, enabled in output_channels.items():
                            try:
                                output_ch = int(output_ch)
                                # Validate output channel is within range
                                if output_ch < 0 or output_ch >= router.OUTPUT_CHANNELS:
                                    continue
                                router.routing_matrix[input_ch][output_ch] = bool(enabled)
                            except (ValueError, TypeError):
                                continue
                    except (ValueError, TypeError):
                        continue
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("Starting Audio Router Server...")
    print("Open http://localhost:5001 in your browser")
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)
