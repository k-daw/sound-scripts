"""
Simple Audio Router
Routes audio from input device to output device with basic level monitoring
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sounddevice as sd
import numpy as np
import threading
import time
import wave
import json
import copy
from datetime import datetime

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

class SimpleAudioRouter:
    def __init__(self):
        self.CHUNK = 1024
        self.RATE = 48000
        self.running = False
        self.input_device = None
        self.output_device = None
        self.input_channels = 2
        self.output_channels = 2
        self.audio_thread = None
        self.current_levels = []
        
        # Config
        self.input_gain_db = 0
        self.output_gain_db = 0
        
        # Routing matrix: routing_matrix[input_channel][output_channel] = enabled (bool)
        self.routing_matrix = {}
        self.use_routing_matrix = False
        self._routing_matrix_lock = threading.Lock()
        
        # Noise gate: per-channel parameters
        # Structure: channel_index -> {threshold, attack, release, hold, range}
        self.noise_gate_params = {}  # channel_index -> dict of parameters
        self.default_noise_gate = {
            'threshold': -40.0,  # dB - level below which gate closes
            'attack': 0.001,     # seconds - time to open gate
            'release': 0.050,    # seconds - time to close gate
            'hold': 0.010,       # seconds - minimum time gate stays open
            'range': -100.0      # dB - reduction when gate is closed (negative = mute)
        }
        
        # Gate state tracking per channel
        self.gate_state = {}  # channel_index -> {is_open, open_time, close_time, current_gain}
        
        # Recording
        self.recording = False
        self.recording_filename = None
        self.recording_start_time = None
        self.recorded_frames = []
        self.wav_file = None
        
    def db_to_linear(self, db):
        """Convert dB to linear gain"""
        return 10 ** (db / 20)
    
    def calculate_levels(self, audio_data):
        """Calculate peak levels in dB for each channel"""
        levels = []
        for i in range(audio_data.shape[1]):
            peak = np.max(np.abs(audio_data[:, i]))
            if peak < 1e-10:
                db = -100.0
            else:
                db = float(20 * np.log10(peak))  # Convert to Python float for JSON serialization
            levels.append(db)
        return levels
    
    def process_audio(self):
        """Main audio processing loop"""
        try:
            print(f"Starting audio streams...")
            print(f"  Input: Device {self.input_device}, {self.input_channels} channels")
            print(f"  Output: Device {self.output_device}, {self.output_channels} channels")
            
            input_stream = sd.InputStream(
                device=self.input_device,
                channels=self.input_channels,
                samplerate=self.RATE,
                blocksize=self.CHUNK,
                dtype=np.float32
            )
            
            output_stream = sd.OutputStream(
                device=self.output_device,
                channels=self.output_channels,
                samplerate=self.RATE,
                blocksize=self.CHUNK,
                dtype=np.float32
            )
            
            input_stream.start()
            output_stream.start()
            print("✓ Audio streams started")
            
            frame_count = 0
            
            while self.running:
                # Read audio
                indata, overflowed = input_stream.read(self.CHUNK)
                
                if overflowed:
                    print("!", end="", flush=True)
                
                # Apply input gain
                input_gain = self.db_to_linear(self.input_gain_db)
                indata_gained = indata * input_gain
                
                # Calculate levels
                levels = self.calculate_levels(indata_gained)
                self.current_levels = levels
                
                # Thread-safe copy of routing matrix
                with self._routing_matrix_lock:
                    use_matrix = self.use_routing_matrix
                    if use_matrix:
                        routing_matrix_copy = copy.deepcopy(self.routing_matrix)
                    else:
                        routing_matrix_copy = None
                
                # Apply output gain
                output_gain = self.db_to_linear(self.output_gain_db)
                
                # Create output
                outdata = np.zeros((self.CHUNK, self.output_channels), dtype=np.float32)
                
                # Process noise gates for all channels
                chunk_time = self.CHUNK / self.RATE  # Time duration of this chunk
                current_time = time.time()
                
                # Calculate gate gains for each channel
                gate_gains = np.ones(self.input_channels, dtype=np.float32)
                for input_ch in range(self.input_channels):
                    params = self.noise_gate_params.get(input_ch, self.default_noise_gate)
                    state = self.gate_state.get(input_ch, {
                        'is_open': False,
                        'open_time': 0.0,
                        'close_time': 0.0,
                        'current_gain': 0.0
                    })
                    
                    channel_level = levels[input_ch] if input_ch < len(levels) else -100.0
                    threshold = params['threshold']
                    attack = params['attack']
                    release = params['release']
                    hold = params['hold']
                    range_db = params['range']
                    range_linear = self.db_to_linear(range_db)
                    
                    # Determine if gate should be open based on level
                    should_be_open = channel_level >= threshold
                    
                    # Update gate state with attack/release/hold logic
                    if should_be_open:
                        if not state['is_open']:
                            # Start opening
                            state['open_time'] = current_time
                            state['is_open'] = True
                        # Gate is or should be open
                        if state['current_gain'] < 1.0:
                            # Apply attack
                            attack_rate = chunk_time / attack if attack > 0 else 1.0
                            state['current_gain'] = min(1.0, state['current_gain'] + attack_rate)
                    else:
                        # Signal below threshold
                        if state['is_open']:
                            # Check hold time
                            time_since_open = current_time - state['open_time']
                            if time_since_open < hold:
                                # Still in hold period, keep gate open
                                should_be_open = True
                            else:
                                # Start closing
                                state['close_time'] = current_time
                                state['is_open'] = False
                        
                        if not should_be_open:
                            # Apply release
                            release_rate = chunk_time / release if release > 0 else 1.0
                            state['current_gain'] = max(range_linear, state['current_gain'] - release_rate)
                    
                    # Store updated state
                    self.gate_state[input_ch] = state
                    gate_gains[input_ch] = state['current_gain']
                
                if use_matrix:
                    # Routing matrix mode
                    active_routes_per_output = np.zeros(self.output_channels, dtype=np.int32)
                    
                    for input_ch in range(self.input_channels):
                        # Apply gate gain to channel
                        gate_gain = gate_gains[input_ch]
                        if gate_gain <= 0.001:  # Effectively muted
                            continue
                        
                        if input_ch in routing_matrix_copy:
                            for output_ch in range(self.output_channels):
                                if output_ch in routing_matrix_copy[input_ch] and routing_matrix_copy[input_ch][output_ch]:
                                    channel_data = indata_gained[:, input_ch] * gate_gain * output_gain
                                    outdata[:, output_ch] += channel_data
                                    active_routes_per_output[output_ch] += 1
                    
                    # Normalize if multiple inputs routed to same output
                    for output_ch in range(self.output_channels):
                        if active_routes_per_output[output_ch] > 1:
                            outdata[:, output_ch] /= active_routes_per_output[output_ch]
                else:
                    # Simple mixing mode (original behavior)
                    # Apply noise gate: mix channels with their gate gains applied
                    channels_to_mix = []
                    for input_ch in range(self.input_channels):
                        gate_gain = gate_gains[input_ch]
                        if gate_gain > 0.001:  # Only include if not effectively muted
                            channels_to_mix.append((input_ch, gate_gain))
                    
                    if len(channels_to_mix) == 0:
                        # All channels gated, output silence
                        mixed = np.zeros(self.CHUNK, dtype=np.float32)
                    elif len(channels_to_mix) == 1:
                        input_ch, gate_gain = channels_to_mix[0]
                        mixed = indata_gained[:, input_ch] * gate_gain
                    else:
                        # Sum channels with their gate gains applied
                        mixed = np.sum([indata_gained[:, ch] * gain for ch, gain in channels_to_mix], axis=0) / np.sqrt(len(channels_to_mix))
                    
                    mixed = mixed * output_gain
                    mixed = np.clip(mixed, -1.0, 1.0)
                    
                    for ch in range(self.output_channels):
                        outdata[:, ch] = mixed
                
                # Final clip
                outdata = np.clip(outdata, -1.0, 1.0)
                
                # Record if enabled
                if self.recording:
                    audio_int16 = (outdata * 32767).astype(np.int16)
                    self.recorded_frames.append(audio_int16.tobytes())
                
                # Write output
                output_stream.write(outdata)
                
                # Send levels to frontend every 10 frames
                frame_count += 1
                if frame_count % 10 == 0:
                    # Pad to 10 channels for frontend, ensure all are Python floats
                    padded_levels = [float(l) for l in levels] + [-100.0] * (10 - len(levels))
                    socketio.emit('levels', {
                        'levels': padded_levels[:10],
                        'active_channel': None  # Simple router - no channel selection
                    })
            
            # Cleanup
            input_stream.stop()
            output_stream.stop()
            input_stream.close()
            output_stream.close()
            print("✓ Audio streams stopped")
            
        except Exception as e:
            print(f"✗ Error in audio processing: {e}")
            import traceback
            traceback.print_exc()
            self.running = False
            socketio.emit('error', {'message': str(e)})
    
    def start(self, input_device, output_device):
        """Start routing audio"""
        if self.running:
            return False
        
        # Get device info
        try:
            input_info = sd.query_devices(input_device)
            output_info = sd.query_devices(output_device)
            
            self.input_channels = min(int(input_info['max_input_channels']), 10)
            self.output_channels = min(int(output_info['max_output_channels']), 8)
            
            # Initialize routing matrix
            with self._routing_matrix_lock:
                new_routing_matrix = {}
                for i in range(self.input_channels):
                    if i in self.routing_matrix:
                        new_routing_matrix[i] = {
                            out_ch: self.routing_matrix[i].get(out_ch, False)
                            for out_ch in range(self.output_channels)
                        }
                    else:
                        new_routing_matrix[i] = {}
                self.routing_matrix = new_routing_matrix
            
            # Initialize noise gate parameters for new channels (if not already set)
            for i in range(self.input_channels):
                if i not in self.noise_gate_params:
                    self.noise_gate_params[i] = copy.deepcopy(self.default_noise_gate)
                if i not in self.gate_state:
                    self.gate_state[i] = {
                        'is_open': False,
                        'open_time': 0.0,
                        'close_time': 0.0,
                        'current_gain': 0.0  # linear gain (0.0 to 1.0)
                    }
            
            self.input_device = input_device
            self.output_device = output_device
            self.running = True
            
            # Start audio thread
            self.audio_thread = threading.Thread(target=self.process_audio)
            self.audio_thread.start()
            
            return True
        except Exception as e:
            print(f"Error starting router: {e}")
            return False
    
    def stop(self):
        """Stop routing audio"""
        self.running = False
        if self.audio_thread:
            self.audio_thread.join(timeout=2)
    
    def get_config(self):
        """Get current configuration"""
        with self._routing_matrix_lock:
            routing_matrix_copy = copy.deepcopy(self.routing_matrix)
            use_routing_matrix = self.use_routing_matrix
        return {
            'input_gain_db': self.input_gain_db,
            'output_gain_db': self.output_gain_db,
            'input_device': self.input_device,
            'output_device': self.output_device,
            'use_routing_matrix': use_routing_matrix,
            'routing_matrix': routing_matrix_copy,
            'input_channels': self.input_channels,
            'output_channels': self.output_channels,
            'noise_gate_params': {str(k): copy.deepcopy(v) for k, v in self.noise_gate_params.items()},
            'default_noise_gate': copy.deepcopy(self.default_noise_gate),
            # Frontend expects these but we don't use them in simple router
            'threshold_db': -40,
            'priority_channels': [1, 3],
            'channel_3_exclusive': True,
            'channel_hold_time': 0.5
        }
    
    def update_config(self, config):
        """Update configuration"""
        if 'input_gain_db' in config:
            self.input_gain_db = float(config['input_gain_db'])
        if 'output_gain_db' in config:
            self.output_gain_db = float(config['output_gain_db'])
        if 'use_routing_matrix' in config:
            with self._routing_matrix_lock:
                self.use_routing_matrix = config['use_routing_matrix']
        if 'routing_matrix' in config:
            with self._routing_matrix_lock:
                for input_ch, output_channels in config['routing_matrix'].items():
                    input_ch = int(input_ch)
                    if input_ch not in self.routing_matrix:
                        self.routing_matrix[input_ch] = {}
                    for output_ch, enabled in output_channels.items():
                        output_ch = int(output_ch)
                        self.routing_matrix[input_ch][output_ch] = bool(enabled)
        if 'noise_gate_params' in config:
            for channel, params in config['noise_gate_params'].items():
                channel = int(channel)
                if channel not in self.noise_gate_params:
                    self.noise_gate_params[channel] = copy.deepcopy(self.default_noise_gate)
                # Update individual parameters
                if isinstance(params, dict):
                    for key, value in params.items():
                        if key in self.noise_gate_params[channel]:
                            self.noise_gate_params[channel][key] = float(value)
        if 'default_noise_gate' in config:
            for key, value in config['default_noise_gate'].items():
                if key in self.default_noise_gate:
                    self.default_noise_gate[key] = float(value)
    
    def start_recording(self, filename=None):
        """Start recording to WAV file"""
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"recording_{timestamp}.wav"
        
        self.wav_file = wave.open(filename, 'wb')
        self.wav_file.setnchannels(self.output_channels)
        self.wav_file.setsampwidth(2)
        self.wav_file.setframerate(self.RATE)
        self.recording = True
        self.recorded_frames = []
        self.recording_filename = filename
        self.recording_start_time = time.time()
        return filename
    
    def stop_recording(self):
        """Stop recording and save file"""
        if self.wav_file:
            for frame in self.recorded_frames:
                self.wav_file.writeframes(frame)
            self.wav_file.close()
            self.wav_file = None
            self.recording = False
            filename = self.recording_filename
            self.recording_filename = None
            self.recording_start_time = None
            return filename
        return None

# Global router instance
router = SimpleAudioRouter()

# API Routes

@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get list of available audio devices"""
    try:
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
    """Start audio routing"""
    try:
        data = request.json
        input_device = data.get('input_device')
        output_device = data.get('output_device')
        
        if input_device is None or output_device is None:
            return jsonify({'error': 'Input and output devices required'}), 400
        
        success = router.start(input_device, output_device)
        if success:
            return jsonify({'status': 'started'})
        else:
            return jsonify({'error': 'Router already running'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop', methods=['POST'])
def stop_router():
    """Stop audio routing"""
    try:
        router.stop()
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get router status"""
    try:
        return jsonify({
            'running': router.running,
            'active_channel': None,
            'recording': router.recording,
            'recording_filename': router.recording_filename,
            'recording_duration': time.time() - router.recording_start_time if router.recording_start_time else 0
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get router configuration"""
    try:
        return jsonify(router.get_config())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['POST'])
def update_config():
    """Update router configuration"""
    try:
        data = request.json
        router.update_config(data)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record/start', methods=['POST'])
def start_recording():
    """Start recording"""
    try:
        data = request.json or {}
        filename = data.get('filename')
        filename = router.start_recording(filename)
        return jsonify({'status': 'recording', 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/record/stop', methods=['POST'])
def stop_recording():
    """Stop recording"""
    try:
        filename = router.stop_recording()
        return jsonify({'status': 'stopped', 'filename': filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/presets', methods=['GET'])
def get_presets():
    """Get saved presets"""
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
    """Save a preset"""
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

# Socket.IO event handlers
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

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
            'input_channels': router.input_channels,
            'output_channels': router.output_channels
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
                for input_ch, output_channels in data['routing_matrix'].items():
                    input_ch = int(input_ch)
                    if input_ch not in router.routing_matrix:
                        router.routing_matrix[input_ch] = {}
                    for output_ch, enabled in output_channels.items():
                        output_ch = int(output_ch)
                        router.routing_matrix[input_ch][output_ch] = bool(enabled)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/noise-gate', methods=['GET'])
def get_noise_gate():
    """Get noise gate parameters for all channels"""
    try:
        return jsonify({
            'noise_gate_params': {str(k): copy.deepcopy(v) for k, v in router.noise_gate_params.items()},
            'default_noise_gate': copy.deepcopy(router.default_noise_gate),
            'input_channels': router.input_channels
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/noise-gate', methods=['POST'])
def update_noise_gate():
    """Update noise gate parameters for a specific channel or set default"""
    try:
        data = request.json
        if 'channel' in data:
            # Update specific channel parameters
            channel = int(data['channel'])
            if channel not in router.noise_gate_params:
                router.noise_gate_params[channel] = copy.deepcopy(router.default_noise_gate)
            
            # Update individual parameters if provided
            if 'threshold' in data:
                router.noise_gate_params[channel]['threshold'] = float(data['threshold'])
            if 'attack' in data:
                router.noise_gate_params[channel]['attack'] = float(data['attack'])
            if 'release' in data:
                router.noise_gate_params[channel]['release'] = float(data['release'])
            if 'hold' in data:
                router.noise_gate_params[channel]['hold'] = float(data['hold'])
            if 'range' in data:
                router.noise_gate_params[channel]['range'] = float(data['range'])
            if 'params' in data:
                # Bulk update all parameters for this channel
                for key, value in data['params'].items():
                    if key in router.noise_gate_params[channel]:
                        router.noise_gate_params[channel][key] = float(value)
        elif 'default_noise_gate' in data:
            # Update default parameters
            for key, value in data['default_noise_gate'].items():
                if key in router.default_noise_gate:
                    router.default_noise_gate[key] = float(value)
        elif 'params' in data:
            # Bulk update multiple channels
            for channel, params in data['params'].items():
                channel = int(channel)
                if channel not in router.noise_gate_params:
                    router.noise_gate_params[channel] = copy.deepcopy(router.default_noise_gate)
                for key, value in params.items():
                    if key in router.noise_gate_params[channel]:
                        router.noise_gate_params[channel][key] = float(value)
        return jsonify({'status': 'updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 50)
    print("Simple Audio Router")
    print("=" * 50)
    print("Starting server on http://localhost:5001")
    print("Open the web interface to control the router")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)

