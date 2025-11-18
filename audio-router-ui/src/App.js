import React, { useState, useEffect } from 'react';
import { Activity, Settings, Play, Square, Circle, Download, Upload, Mic, RefreshCw } from 'lucide-react';
import io from 'socket.io-client';

const API_BASE = 'http://localhost:5001/api';
const socket = io('http://localhost:5001');

function App() {
  const [devices, setDevices] = useState([]);
  const [inputDevice, setInputDevice] = useState(null);
  const [outputDevice, setOutputDevice] = useState(null);
  const [running, setRunning] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [recordingFilename, setRecordingFilename] = useState('');
  
  const [levels, setLevels] = useState(Array(10).fill(-100));
  const [activeChannel, setActiveChannel] = useState(null);
  
  const [config, setConfig] = useState({
    threshold_db: -40,
    input_gain_db: 0,
    output_gain_db: 0,
    priority_channels: [1, 3],
    channel_3_exclusive: true,
    channel_hold_time: 0.5
  });
  
  const [showConfig, setShowConfig] = useState(false);
  const [presets, setPresets] = useState([]);
  const [presetName, setPresetName] = useState('');
  const [refreshingDevices, setRefreshingDevices] = useState(false);
  const [connected, setConnected] = useState(false);
  const [lastLevelUpdate, setLastLevelUpdate] = useState(null);
  const [showDebug, setShowDebug] = useState(false);
  const [debugInfo, setDebugInfo] = useState(null);
  
  // Routing matrix state
  const [routingMatrix, setRoutingMatrix] = useState({});
  const [useRoutingMatrix, setUseRoutingMatrix] = useState(false);
  const [inputChannels, setInputChannels] = useState(10);
  const [outputChannels, setOutputChannels] = useState(2);
  const [showRoutingMatrix, setShowRoutingMatrix] = useState(false);
  
  // Noise gate state
  const [noiseGateParams, setNoiseGateParams] = useState({}); // channel -> {threshold, attack, release, hold, range}
  const [defaultNoiseGate, setDefaultNoiseGate] = useState({
    threshold: -40.0,
    attack: 0.001,
    release: 0.050,
    hold: 0.010,
    range: -100.0
  });
  const [showNoiseGate, setShowNoiseGate] = useState(false);
  const [expandedGateChannel, setExpandedGateChannel] = useState(null); // Which channel has expanded controls

  // Fetch devices on mount
  useEffect(() => {
    fetchDevices();
    fetchConfig();
    fetchPresets();
    fetchRoutingMatrix();
    fetchNoiseGate();
    
    const statusInterval = setInterval(fetchStatus, 1000);
    
    return () => clearInterval(statusInterval);
  }, []);
  
  // Fetch routing matrix when devices change
  useEffect(() => {
    if (inputDevice !== null && outputDevice !== null) {
      // Update input channels from selected device
      const selectedInputDevice = devices.find(d => d.index === inputDevice);
      const selectedOutputDevice = devices.find(d => d.index === outputDevice);
      
      if (selectedInputDevice) {
        setInputChannels(selectedInputDevice.input_channels);
      }
      if (selectedOutputDevice) {
        setOutputChannels(selectedOutputDevice.output_channels);
      }
      
      // Small delay to let backend update channel counts
      // Note: fetchRoutingMatrix will only update channels if router is running
      const timer = setTimeout(() => {
        fetchRoutingMatrix();
        fetchNoiseGate();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [inputDevice, outputDevice, devices]);

  // Listen for real-time level updates
  useEffect(() => {
    socket.on('levels', (data) => {
      setLevels(data.levels);
      setActiveChannel(data.active_channel);
      setLastLevelUpdate(new Date().toLocaleTimeString());
    });
    
    socket.on('error', (data) => {
      console.error('Backend error:', data.message);
      alert(`Backend error: ${data.message}`);
      setRunning(false);
    });
    
    socket.on('connect', () => {
      console.log('WebSocket connected');
      setConnected(true);
    });
    
    socket.on('disconnect', () => {
      console.log('WebSocket disconnected');
      setConnected(false);
    });
    
    return () => {
      socket.off('levels');
      socket.off('error');
      socket.off('connect');
      socket.off('disconnect');
    };
  }, []);

  const fetchDevices = async () => {
    try {
      setRefreshingDevices(true);
      const res = await fetch(`${API_BASE}/devices`);
      const data = await res.json();
      setDevices(data.devices);
    } catch (err) {
      console.error('Failed to fetch devices:', err);
      alert('Failed to fetch devices. Make sure the backend is running.');
    } finally {
      setRefreshingDevices(false);
    }
  };

  const fetchConfig = async () => {
    try {
      const res = await fetch(`${API_BASE}/config`);
      const data = await res.json();
      setConfig(data);
      setInputDevice(data.input_device);
      setOutputDevice(data.output_device);
      if (data.use_routing_matrix !== undefined) {
        setUseRoutingMatrix(data.use_routing_matrix);
      }
      if (data.routing_matrix) {
        setRoutingMatrix(data.routing_matrix);
      }
      // Only update channel counts from backend if router is running
      // Otherwise, use device info which is more reliable
      if (running) {
        if (data.input_channels) {
          setInputChannels(data.input_channels);
        }
        if (data.output_channels) {
          setOutputChannels(data.output_channels);
        }
      } else {
        // If router not running, update from device info if available
        const selectedInputDevice = devices.find(d => d.index === data.input_device);
        const selectedOutputDevice = devices.find(d => d.index === data.output_device);
        if (selectedInputDevice) {
          setInputChannels(selectedInputDevice.input_channels);
        }
        if (selectedOutputDevice) {
          setOutputChannels(selectedOutputDevice.output_channels);
        }
      }
      if (data.noise_gate_params) {
        const params = {};
        for (const [key, value] of Object.entries(data.noise_gate_params)) {
          params[parseInt(key)] = value;
        }
        setNoiseGateParams(params);
      }
      if (data.default_noise_gate) {
        setDefaultNoiseGate(data.default_noise_gate);
      }
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  };
  
  const fetchRoutingMatrix = async () => {
    try {
      const res = await fetch(`${API_BASE}/routing-matrix`);
      const data = await res.json();
      if (data.use_routing_matrix !== undefined) {
        setUseRoutingMatrix(data.use_routing_matrix);
      }
      if (data.routing_matrix) {
        setRoutingMatrix(data.routing_matrix);
      }
      // Only update channel counts from backend if router is running
      // Otherwise, use device info which is more reliable
      if (running) {
        if (data.input_channels) {
          setInputChannels(data.input_channels);
        }
        if (data.output_channels) {
          setOutputChannels(data.output_channels);
        }
      }
    } catch (err) {
      console.error('Failed to fetch routing matrix:', err);
    }
  };
  
  const updateRoutingMatrix = async (inputCh, outputCh, enabled) => {
    try {
      // Update local state first for immediate UI feedback
      const newMatrix = { ...routingMatrix };
      if (!newMatrix[inputCh]) {
        newMatrix[inputCh] = {};
      }
      newMatrix[inputCh][outputCh] = enabled;
      setRoutingMatrix(newMatrix);
      
      // Send update to backend
      await fetch(`${API_BASE}/routing-matrix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          routing_matrix: newMatrix
        })
      });
    } catch (err) {
      console.error('Failed to update routing matrix:', err);
      // Revert on error
      fetchRoutingMatrix();
    }
  };
  
  const toggleRoutingMatrixMode = async () => {
    try {
      const newValue = !useRoutingMatrix;
      setUseRoutingMatrix(newValue);
      await fetch(`${API_BASE}/routing-matrix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          use_routing_matrix: newValue
        })
      });
    } catch (err) {
      console.error('Failed to toggle routing matrix mode:', err);
      fetchRoutingMatrix();
    }
  };
  
  const fetchNoiseGate = async () => {
    try {
      const res = await fetch(`${API_BASE}/noise-gate`);
      const data = await res.json();
      if (data.noise_gate_params) {
        // Convert string keys to numbers
        const params = {};
        for (const [key, value] of Object.entries(data.noise_gate_params)) {
          params[parseInt(key)] = value;
        }
        setNoiseGateParams(params);
      }
      if (data.default_noise_gate) {
        setDefaultNoiseGate(data.default_noise_gate);
      }
    } catch (err) {
      console.error('Failed to fetch noise gate:', err);
    }
  };
  
  const updateNoiseGateParam = async (channel, paramName, value) => {
    try {
      // Update local state first for immediate UI feedback
      const newParams = { ...noiseGateParams };
      if (!newParams[channel]) {
        newParams[channel] = { ...defaultNoiseGate };
      }
      newParams[channel] = { ...newParams[channel], [paramName]: value };
      setNoiseGateParams(newParams);
      
      // Send update to backend
      await fetch(`${API_BASE}/noise-gate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: channel,
          [paramName]: value
        })
      });
    } catch (err) {
      console.error('Failed to update noise gate:', err);
      // Revert on error
      fetchNoiseGate();
    }
  };
  
  const updateDefaultNoiseGateParam = async (paramName, value) => {
    try {
      const newDefault = { ...defaultNoiseGate, [paramName]: value };
      setDefaultNoiseGate(newDefault);
      await fetch(`${API_BASE}/noise-gate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          default_noise_gate: { [paramName]: value }
        })
      });
    } catch (err) {
      console.error('Failed to update default noise gate:', err);
      fetchNoiseGate();
    }
  };
  
  const getGateParam = (channel, paramName) => {
    if (noiseGateParams[channel] && noiseGateParams[channel][paramName] !== undefined) {
      return noiseGateParams[channel][paramName];
    }
    return defaultNoiseGate[paramName];
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/status`);
      const data = await res.json();
      setRunning(data.running);
      setRecording(data.recording);
      setRecordingFilename(data.recording_filename || '');
      setRecordingDuration(Math.floor(data.recording_duration || 0));
    } catch (err) {
      console.error('Failed to fetch status:', err);
    }
  };

  const fetchPresets = async () => {
    try {
      const res = await fetch(`${API_BASE}/presets`);
      const data = await res.json();
      setPresets(data.presets || []);
    } catch (err) {
      console.error('Failed to fetch presets:', err);
    }
  };

  const updateConfig = async (newConfig) => {
    try {
      await fetch(`${API_BASE}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newConfig)
      });
      setConfig({ ...config, ...newConfig });
    } catch (err) {
      console.error('Failed to update config:', err);
    }
  };

  const startRouter = async () => {
    if (inputDevice === null || outputDevice === null) {
      alert('Please select both input and output devices');
      return;
    }
    
    // Check if selected input device has enough channels
    const selectedInputDevice = devices.find(d => d.index === inputDevice);
    if (selectedInputDevice && selectedInputDevice.input_channels < 2) {
      alert(`Warning: Selected input device only has ${selectedInputDevice.input_channels} channel(s). Multi-channel routing may not work properly.`);
    }
    
    try {
      const response = await fetch(`${API_BASE}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input_device: inputDevice, output_device: outputDevice })
      });
      
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || 'Failed to start router');
      }
      
      setRunning(true);
    } catch (err) {
      console.error('Failed to start router:', err);
      alert(`Failed to start router: ${err.message}`);
    }
  };

  const stopRouter = async () => {
    try {
      await fetch(`${API_BASE}/stop`, { method: 'POST' });
      setRunning(false);
    } catch (err) {
      console.error('Failed to stop router:', err);
    }
  };

  const startRecording = async () => {
    try {
      await fetch(`${API_BASE}/record/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });
      setRecording(true);
    } catch (err) {
      console.error('Failed to start recording:', err);
    }
  };

  const stopRecording = async () => {
    try {
      await fetch(`${API_BASE}/record/stop`, { method: 'POST' });
      setRecording(false);
    } catch (err) {
      console.error('Failed to stop recording:', err);
    }
  };

  const savePreset = async () => {
    if (!presetName) {
      alert('Please enter a preset name');
      return;
    }
    
    try {
      await fetch(`${API_BASE}/presets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: presetName })
      });
      setPresetName('');
      fetchPresets();
    } catch (err) {
      console.error('Failed to save preset:', err);
    }
  };

  const loadPreset = (preset) => {
    updateConfig(preset.config);
    setInputDevice(preset.config.input_device);
    setOutputDevice(preset.config.output_device);
  };

  const getLevelColor = (db) => {
    if (db > -10) return 'bg-red-500';
    if (db > -20) return 'bg-yellow-500';
    return 'bg-green-500';
  };

  const getLevelWidth = (db) => {
    const normalized = Math.max(0, Math.min(100, (db + 60) / 60 * 100));
    return `${normalized}%`;
  };

  const formatDuration = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <div className="bg-gradient-to-br from-blue-500 to-purple-600 p-3 rounded-xl">
              <Activity className="w-8 h-8" />
            </div>
            <div>
              <h1 className="text-3xl font-bold">Zoom H8 Audio Router</h1>
              <div className="flex items-center gap-2">
                <p className="text-slate-400 text-sm">Intelligent multi-channel routing</p>
                <div className={`flex items-center gap-1 text-xs ${connected ? 'text-green-400' : 'text-red-400'}`}>
                  <Circle className={`w-2 h-2 fill-current ${connected ? 'animate-pulse' : ''}`} />
                  {connected ? 'Connected' : 'Disconnected'}
                </div>
              </div>
            </div>
          </div>
          <button
            onClick={() => setShowConfig(!showConfig)}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition"
          >
            <Settings className="w-5 h-5" />
            Configure
          </button>
        </div>

        {/* Device Selection */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">Audio Devices</h2>
            <button
              onClick={fetchDevices}
              disabled={refreshingDevices || running}
              className="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-800 disabled:cursor-not-allowed rounded-lg transition"
            >
              <RefreshCw className={`w-4 h-4 ${refreshingDevices ? 'animate-spin' : ''}`} />
              Refresh Devices
            </button>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
              <label className="block text-sm font-medium mb-2 text-slate-300">Input Device</label>
              <select
                value={inputDevice !== null ? inputDevice : ''}
                onChange={(e) => setInputDevice(e.target.value === '' ? null : parseInt(e.target.value))}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none"
                disabled={running}
              >
                <option value="">Select device...</option>
                {devices.filter(d => d.input_channels > 0).map(d => (
                  <option key={d.index} value={d.index}>
                    {d.name} ({d.input_channels} ch)
                  </option>
                ))}
              </select>
              {inputDevice !== null && (
                <div className="mt-2 text-xs text-slate-400">
                  {devices.find(d => d.index === inputDevice)?.input_channels} input channels available
                </div>
              )}
            </div>
            
            <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
              <label className="block text-sm font-medium mb-2 text-slate-300">Output Device</label>
              <select
                value={outputDevice !== null ? outputDevice : ''}
                onChange={(e) => setOutputDevice(e.target.value === '' ? null : parseInt(e.target.value))}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none"
                disabled={running}
              >
                <option value="">Select device...</option>
                {devices.filter(d => d.output_channels >= 2).map(d => (
                  <option key={d.index} value={d.index}>
                    {d.name} ({d.output_channels} ch)
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Routing Matrix */}
        <div className="bg-slate-800 rounded-xl p-6 mb-6 border border-slate-700">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold">Channel Routing Matrix</h2>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={useRoutingMatrix}
                  onChange={toggleRoutingMatrixMode}
                  className="w-5 h-5 rounded"
                />
                <span className="text-sm">Enable Matrix Routing</span>
              </label>
              <button
                onClick={() => setShowRoutingMatrix(!showRoutingMatrix)}
                className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition"
              >
                {showRoutingMatrix ? 'Hide' : 'Show'} Matrix
              </button>
            </div>
          </div>
          
          {showRoutingMatrix && (
            <div className="overflow-x-auto">
              <div className="inline-block min-w-full">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <th className="bg-slate-700 p-2 text-left text-sm font-medium border border-slate-600">
                        Input → Output
                      </th>
                      {Array.from({ length: outputChannels }, (_, i) => (
                        <th
                          key={i}
                          className="bg-slate-700 p-2 text-center text-sm font-medium border border-slate-600 min-w-[60px]"
                        >
                          Out {i + 1}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Array.from({ length: inputChannels }, (_, inputIdx) => (
                      <tr key={inputIdx} className="hover:bg-slate-700/50">
                        <td className="bg-slate-700/30 p-2 text-sm font-medium border border-slate-600">
                          <div className="flex items-center gap-2">
                            <span>Input {inputIdx + 1}</span>
                            {levels[inputIdx] > -90 && (
                              <span className="text-xs text-slate-400">
                                ({levels[inputIdx].toFixed(0)} dB)
                              </span>
                            )}
                          </div>
                        </td>
                        {Array.from({ length: outputChannels }, (_, outputIdx) => {
                          const isEnabled = routingMatrix[inputIdx]?.[outputIdx] || false;
                          return (
                            <td
                              key={outputIdx}
                              className="p-2 text-center border border-slate-600"
                            >
                              <label className="flex items-center justify-center cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={isEnabled}
                                  onChange={(e) =>
                                    updateRoutingMatrix(inputIdx, outputIdx, e.target.checked)
                                  }
                                  disabled={!useRoutingMatrix}
                                  className="w-5 h-5 rounded cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                                />
                              </label>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-3 text-xs text-slate-400">
                {useRoutingMatrix
                  ? 'Check boxes to route input channels to output channels. Multiple inputs can be routed to the same output (they will be mixed).'
                  : 'Enable Matrix Routing to use the routing matrix instead of automatic channel selection.'}
              </p>
            </div>
          )}
        </div>

        {/* Channel Levels */}
        <div className="bg-slate-800 rounded-xl p-6 mb-6 border border-slate-700">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-xl font-semibold">Channel Levels</h2>
              {lastLevelUpdate && (
                <p className="text-xs text-slate-500">Last update: {lastLevelUpdate}</p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setShowNoiseGate(!showNoiseGate)}
                className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition"
              >
                {showNoiseGate ? 'Hide' : 'Show'} Noise Gate
              </button>
              {!useRoutingMatrix && activeChannel && (
                <div className="flex items-center gap-2 px-4 py-2 bg-blue-600 rounded-lg">
                  <Circle className="w-4 h-4 fill-current animate-pulse" />
                  <span className="font-medium">Active: Channel {activeChannel}</span>
                </div>
              )}
            </div>
          </div>
          
          <div className="space-y-3">
            {levels.map((level, idx) => {
              const threshold = getGateParam(idx, 'threshold');
              const isGated = level < threshold;
              
              return (
                <div key={idx} className="space-y-2">
                  <div className="flex items-center gap-3">
                    <div className="w-12 text-sm font-medium text-slate-400">Ch {idx + 1}</div>
                    <div className="flex-1 h-8 bg-slate-700 rounded-lg overflow-hidden relative">
                      <div
                        className={`h-full transition-all duration-100 ${getLevelColor(level)} ${
                          activeChannel === idx + 1 ? 'animate-pulse' : ''
                        } ${isGated ? 'opacity-50' : ''}`}
                        style={{ width: getLevelWidth(level) }}
                      />
                      {/* Noise gate threshold indicator */}
                      <div
                        className="absolute top-0 bottom-0 w-0.5 bg-yellow-400 opacity-60"
                        style={{ left: getLevelWidth(threshold) }}
                        title={`Noise Gate: ${threshold.toFixed(0)} dB`}
                      />
                      {activeChannel === idx + 1 && (
                        <div className="absolute inset-0 border-2 border-white rounded-lg" />
                      )}
                      {isGated && (
                        <div className="absolute inset-0 flex items-center justify-center">
                          <span className="text-xs text-slate-500 font-medium">GATED</span>
                        </div>
                      )}
                    </div>
                    <div className="w-16 text-sm font-mono text-right">
                      {level > -90 ? `${level.toFixed(0)} dB` : '---'}
                    </div>
                  </div>
                  
                  {/* Noise Gate Controls */}
                  {showNoiseGate && (
                    <div className="ml-16 space-y-2 bg-slate-700/30 rounded-lg p-3">
                      <div className="flex items-center gap-2 mb-2">
                        <button
                          onClick={() => setExpandedGateChannel(expandedGateChannel === idx ? null : idx)}
                          className="text-xs text-slate-300 hover:text-white transition"
                        >
                          {expandedGateChannel === idx ? '▼' : '▶'} Advanced
                        </button>
                        <span className="text-xs text-slate-500">|</span>
                        <span className="text-xs text-slate-400">Channel {idx + 1} Gate</span>
                      </div>
                      
                      {/* Basic: Threshold */}
                      <div className="flex items-center gap-3">
                        <label className="text-xs text-slate-400 w-20">Threshold:</label>
                        <input
                          type="range"
                          min="-60"
                          max="-10"
                          step="1"
                          value={threshold}
                          onChange={(e) => updateNoiseGateParam(idx, 'threshold', parseFloat(e.target.value))}
                          className="flex-1"
                        />
                        <div className="w-16 text-xs font-mono text-right text-slate-300">
                          {threshold.toFixed(0)} dB
                        </div>
                      </div>
                      
                      {/* Advanced Parameters */}
                      {expandedGateChannel === idx && (
                        <div className="mt-3 pt-3 border-t border-slate-600 space-y-2">
                          <div className="flex items-center gap-3">
                            <label className="text-xs text-slate-400 w-20">Attack:</label>
                            <input
                              type="range"
                              min="0.0001"
                              max="0.1"
                              step="0.0001"
                              value={getGateParam(idx, 'attack')}
                              onChange={(e) => updateNoiseGateParam(idx, 'attack', parseFloat(e.target.value))}
                              className="flex-1"
                            />
                            <div className="w-20 text-xs font-mono text-right text-slate-300">
                              {(getGateParam(idx, 'attack') * 1000).toFixed(1)} ms
                            </div>
                          </div>
                          
                          <div className="flex items-center gap-3">
                            <label className="text-xs text-slate-400 w-20">Release:</label>
                            <input
                              type="range"
                              min="0.001"
                              max="1.0"
                              step="0.001"
                              value={getGateParam(idx, 'release')}
                              onChange={(e) => updateNoiseGateParam(idx, 'release', parseFloat(e.target.value))}
                              className="flex-1"
                            />
                            <div className="w-20 text-xs font-mono text-right text-slate-300">
                              {(getGateParam(idx, 'release') * 1000).toFixed(0)} ms
                            </div>
                          </div>
                          
                          <div className="flex items-center gap-3">
                            <label className="text-xs text-slate-400 w-20">Hold:</label>
                            <input
                              type="range"
                              min="0.001"
                              max="2.0"
                              step="0.001"
                              value={getGateParam(idx, 'hold')}
                              onChange={(e) => updateNoiseGateParam(idx, 'hold', parseFloat(e.target.value))}
                              className="flex-1"
                            />
                            <div className="w-20 text-xs font-mono text-right text-slate-300">
                              {(getGateParam(idx, 'hold') * 1000).toFixed(0)} ms
                            </div>
                          </div>
                          
                          <div className="flex items-center gap-3">
                            <label className="text-xs text-slate-400 w-20">Range:</label>
                            <input
                              type="range"
                              min="-100"
                              max="0"
                              step="1"
                              value={getGateParam(idx, 'range')}
                              onChange={(e) => updateNoiseGateParam(idx, 'range', parseFloat(e.target.value))}
                              className="flex-1"
                            />
                            <div className="w-20 text-xs font-mono text-right text-slate-300">
                              {getGateParam(idx, 'range').toFixed(0)} dB
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          
          {showNoiseGate && (
            <div className="mt-4 pt-4 border-t border-slate-700">
              <h3 className="text-sm font-semibold mb-3 text-slate-300">Default Gate Parameters</h3>
              <div className="space-y-2">
                <div className="flex items-center gap-3">
                  <label className="text-xs text-slate-400 w-20">Threshold:</label>
                  <input
                    type="range"
                    min="-60"
                    max="-10"
                    step="1"
                    value={defaultNoiseGate.threshold}
                    onChange={(e) => updateDefaultNoiseGateParam('threshold', parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <div className="w-16 text-xs font-mono text-right text-slate-300">
                    {defaultNoiseGate.threshold.toFixed(0)} dB
                  </div>
                </div>
                
                <div className="flex items-center gap-3">
                  <label className="text-xs text-slate-400 w-20">Attack:</label>
                  <input
                    type="range"
                    min="0.0001"
                    max="0.1"
                    step="0.0001"
                    value={defaultNoiseGate.attack}
                    onChange={(e) => updateDefaultNoiseGateParam('attack', parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <div className="w-20 text-xs font-mono text-right text-slate-300">
                    {(defaultNoiseGate.attack * 1000).toFixed(1)} ms
                  </div>
                </div>
                
                <div className="flex items-center gap-3">
                  <label className="text-xs text-slate-400 w-20">Release:</label>
                  <input
                    type="range"
                    min="0.001"
                    max="1.0"
                    step="0.001"
                    value={defaultNoiseGate.release}
                    onChange={(e) => updateDefaultNoiseGateParam('release', parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <div className="w-20 text-xs font-mono text-right text-slate-300">
                    {(defaultNoiseGate.release * 1000).toFixed(0)} ms
                  </div>
                </div>
                
                <div className="flex items-center gap-3">
                  <label className="text-xs text-slate-400 w-20">Hold:</label>
                  <input
                    type="range"
                    min="0.001"
                    max="2.0"
                    step="0.001"
                    value={defaultNoiseGate.hold}
                    onChange={(e) => updateDefaultNoiseGateParam('hold', parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <div className="w-20 text-xs font-mono text-right text-slate-300">
                    {(defaultNoiseGate.hold * 1000).toFixed(0)} ms
                  </div>
                </div>
                
                <div className="flex items-center gap-3">
                  <label className="text-xs text-slate-400 w-20">Range:</label>
                  <input
                    type="range"
                    min="-100"
                    max="0"
                    step="1"
                    value={defaultNoiseGate.range}
                    onChange={(e) => updateDefaultNoiseGateParam('range', parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <div className="w-20 text-xs font-mono text-right text-slate-300">
                    {defaultNoiseGate.range.toFixed(0)} dB
                  </div>
                </div>
              </div>
              <p className="mt-3 text-xs text-slate-500">
                Noise gate parameters control when and how channels are muted. Threshold sets the level, attack/release control smoothness, hold prevents rapid switching, and range sets reduction when closed.
              </p>
            </div>
          )}
        </div>

        {/* Configuration Panel */}
        {showConfig && (
          <div className="bg-slate-800 rounded-xl p-6 mb-6 border border-slate-700">
            <h2 className="text-xl font-semibold mb-4">Configuration</h2>
            
            <div className="grid grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-medium mb-2 text-slate-300">
                  Threshold: {config.threshold_db} dB
                </label>
                <input
                  type="range"
                  min="-60"
                  max="-10"
                  step="1"
                  value={config.threshold_db}
                  onChange={(e) => updateConfig({ threshold_db: parseFloat(e.target.value) })}
                  className="w-full"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-2 text-slate-300">
                  Input Gain: {config.input_gain_db > 0 ? '+' : ''}{config.input_gain_db} dB
                </label>
                <input
                  type="range"
                  min="-30"
                  max="30"
                  step="1"
                  value={config.input_gain_db}
                  onChange={(e) => updateConfig({ input_gain_db: parseFloat(e.target.value) })}
                  className="w-full"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-2 text-slate-300">
                  Output Gain: {config.output_gain_db > 0 ? '+' : ''}{config.output_gain_db} dB
                </label>
                <input
                  type="range"
                  min="-30"
                  max="30"
                  step="1"
                  value={config.output_gain_db}
                  onChange={(e) => updateConfig({ output_gain_db: parseFloat(e.target.value) })}
                  className="w-full"
                />
              </div>
              
              <div>
                <label className="block text-sm font-medium mb-2 text-slate-300">
                  Hold Time: {config.channel_hold_time} sec
                </label>
                <input
                  type="range"
                  min="0.1"
                  max="2"
                  step="0.1"
                  value={config.channel_hold_time}
                  onChange={(e) => updateConfig({ channel_hold_time: parseFloat(e.target.value) })}
                  className="w-full"
                />
              </div>
            </div>
            
            <div className="mt-6 flex items-center gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.channel_3_exclusive}
                  onChange={(e) => updateConfig({ channel_3_exclusive: e.target.checked })}
                  className="w-5 h-5 rounded"
                />
                <span className="text-sm">Channel 3 Exclusive Mode</span>
              </label>
            </div>
            
            {/* Presets */}
            <div className="mt-6 pt-6 border-t border-slate-700">
              <h3 className="text-lg font-semibold mb-3">Presets</h3>
              <div className="flex gap-2 mb-3">
                <input
                  type="text"
                  placeholder="Preset name..."
                  value={presetName}
                  onChange={(e) => setPresetName(e.target.value)}
                  className="flex-1 bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none"
                />
                <button
                  onClick={savePreset}
                  className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded-lg transition flex items-center gap-2"
                >
                  <Download className="w-4 h-4" />
                  Save
                </button>
              </div>
              <div className="flex flex-wrap gap-2">
                {presets.map((preset, idx) => (
                  <button
                    key={idx}
                    onClick={() => loadPreset(preset)}
                    className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition flex items-center gap-2"
                  >
                    <Upload className="w-3 h-3" />
                    {preset.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Recording Status */}
        {recording && (
          <div className="bg-red-900/20 border border-red-500 rounded-xl p-4 mb-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Circle className="w-5 h-5 fill-red-500 text-red-500 animate-pulse" />
                <div>
                  <div className="font-semibold">Recording</div>
                  <div className="text-sm text-slate-400">{recordingFilename}</div>
                </div>
              </div>
              <div className="text-2xl font-mono">{formatDuration(recordingDuration)}</div>
            </div>
          </div>
        )}

        {/* Control Buttons */}
        <div className="flex gap-4">
          {!running ? (
            <button
              onClick={startRouter}
              className="flex-1 bg-gradient-to-r from-green-600 to-emerald-600 hover:from-green-500 hover:to-emerald-500 text-white font-semibold py-4 px-6 rounded-xl transition flex items-center justify-center gap-3 shadow-lg"
            >
              <Play className="w-6 h-6" />
              Start Router
            </button>
          ) : (
            <button
              onClick={stopRouter}
              className="flex-1 bg-gradient-to-r from-red-600 to-rose-600 hover:from-red-500 hover:to-rose-500 text-white font-semibold py-4 px-6 rounded-xl transition flex items-center justify-center gap-3 shadow-lg"
            >
              <Square className="w-6 h-6" />
              Stop Router
            </button>
          )}
          
          {running && !recording && (
            <button
              onClick={startRecording}
              className="flex-1 bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white font-semibold py-4 px-6 rounded-xl transition flex items-center justify-center gap-3 shadow-lg"
            >
              <Mic className="w-6 h-6" />
              Start Recording
            </button>
          )}
          
          {running && recording && (
            <button
              onClick={stopRecording}
              className="flex-1 bg-gradient-to-r from-orange-600 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white font-semibold py-4 px-6 rounded-xl transition flex items-center justify-center gap-3 shadow-lg"
            >
              <Square className="w-6 h-6" />
              Stop Recording
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;