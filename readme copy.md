# Audio Channel Router - Complete Documentation

## Project Overview

### Purpose
Route multiple audio input channels from a multi-channel audio interface (Zoom H8 with 10 inputs) to a stereo output, with intelligent channel selection logic.

### Key Requirements
- Monitor all 10 input channels simultaneously
- Select ONE active channel at a time based on:
  - Audio level threshold
  - Priority channel list
  - Custom exclusive rules (e.g., if channel 3 is active, only channel 1 can override)
  - Channel hold time to prevent rapid switching
- Route selected channel to stereo output (duplicate mono to L+R)
- Output to virtual audio device for recording in DAW (Audacity, etc.)
- Provide real-time level monitoring display
- Support configurable gain staging (input and output)

---

## System Architecture

### Audio Flow
```
Zoom H8 (10 channels) 
    ↓
Python Script (Channel Selection & Routing)
    ↓
Virtual Audio Device (BlackHole/VB-Cable)
    ↓
Recording Software (Audacity)
```

### Core Components

1. **Input Handler**
   - Reads multi-channel audio from hardware interface
   - Applies input gain for level matching
   - Calculates RMS levels for each channel

2. **Channel Selection Logic**
   - Evaluates all channels against threshold
   - Applies priority rules
   - Implements hold time to prevent switching
   - Handles special exclusive channel rules

3. **Output Router**
   - Takes selected channel
   - Applies output gain
   - Clips to prevent distortion
   - Duplicates to stereo output

4. **Monitoring & Recording**
   - Real-time level display
   - Optional direct WAV file recording for testing
   - Visual indicators for active channel

---

## Technical Specifications

### Audio Parameters
- **Sample Rate**: 48000 Hz (configurable)
- **Block Size**: 1024 samples
- **Input Channels**: 10 (configurable)
- **Output Channels**: 2 (stereo)
- **Data Type**: float32 (-1.0 to +1.0)
- **WAV Output**: 16-bit PCM

### Processing Parameters
- **Threshold**: -40 dB (default, configurable)
- **Input Gain**: 0 dB (default, configurable ±60 dB)
- **Output Gain**: 0 dB (default, configurable ±60 dB)
- **Hold Time**: 0.5 seconds (default, configurable)
- **Priority Channels**: [1, 3] (default, configurable)
- **Level Smoothing**: 5-sample moving average

---

## Channel Selection Algorithm

### Logic Flow
```
1. Calculate smoothed RMS level for all channels
2. Check if current active channel should be held:
   - If hold time not expired AND level > threshold
   - Keep current channel
3. Apply special rules (e.g., Channel 3 Exclusive Mode):
   - If Channel 3 > threshold
   - Only Channel 1 or Channel 3 can be active
4. Check priority channels in order:
   - If any priority channel > threshold
   - Select it
5. Find highest level channel:
   - Select channel with maximum level > threshold
6. If no channels > threshold:
   - Set active channel to None (silence)
```

### Example Logic Implementation
```python
def select_active_channel(levels):
    # Hold current channel if still active
    if active_channel and time_since_switch < hold_time:
        if levels[active_channel] > threshold:
            return active_channel
    
    # Special rule: Channel 3 exclusive
    if channel_3_exclusive and levels[2] > threshold:
        if levels[0] > threshold and levels[0] > levels[2]:
            return 0  # Channel 1
        else:
            return 2  # Channel 3
    
    # Priority channels
    for ch in priority_channels:
        if levels[ch-1] > threshold:
            return ch-1
    
    # Highest level
    active = [(i, lvl) for i, lvl in enumerate(levels) if lvl > threshold]
    if active:
        return max(active, key=lambda x: x[1])[0]
    
    return None
```

---

## Configuration Options

### Basic Settings
| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `threshold_db` | float | -40 | -100 to 0 | Minimum level to activate channel |
| `input_gain_db` | float | 0 | -60 to +60 | Gain applied to input for level matching |
| `output_gain_db` | float | 0 | -60 to +60 | Gain applied to output audio |
| `channel_hold_time` | float | 0.5 | 0.1 to 5.0 | Seconds to hold channel before switching |

### Advanced Settings
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `priority_channels` | list | [1, 3] | Channels checked first (1-indexed) |
| `channel_3_exclusive` | bool | True | When Ch3 active, only Ch1 can override |
| `CHUNK` | int | 1024 | Audio block size (samples) |
| `RATE` | int | 48000 | Sample rate (Hz) |

---

## Dependencies

### Required Python Packages
```bash
pip install sounddevice numpy
```

### System Requirements

**macOS:**
- BlackHole (virtual audio driver)
  - Download: https://existential.audio/blackhole/
  - Install BlackHole 2ch for stereo
  - Or BlackHole 16ch for multi-channel

**Windows:**
- VB-Audio Virtual Cable (free)
  - Download: https://vb-audio.com/Cable/
- Or VoiceMeeter (more features)
  - Download: https://vb-audio.com/Voicemeeter/

**Linux:**
- PulseAudio (built-in)
```bash
pactl load-module module-null-sink sink_name=VirtualOutput
```

---

## Setup Instructions

### 1. Install Virtual Audio Device

**macOS:**
1. Download and install BlackHole 2ch
2. Restart computer (recommended)
3. Open Audio MIDI Setup (Applications → Utilities)
4. Verify BlackHole 2ch appears in device list

**Recommended: Create Aggregate Device**
1. Open Audio MIDI Setup
2. Click + → Create Aggregate Device
3. Check: ✓ BlackHole 2ch
4. Set Clock Source: BlackHole 2ch
5. Rename to "Recording Aggregate"

### 2. Configure Audio Interface
1. Connect Zoom H8 via USB
2. Set H8 to Interface mode
3. Verify H8 appears in system audio devices
4. Set H8 sample rate to 48000 Hz (or match in script)

### 3. Run the Script
```bash
python audio_router.py
```

### 4. Device Selection
- Input device: Select ZOOM H8 Audio Driver
- Output device: Select BlackHole 2ch (or Aggregate Device)

### 5. Configure Settings (Optional)
When prompted, set:
- Input gain (to match H8 display levels)
- Output gain (for recording level)
- Threshold (adjusted for input gain)
- Enable WAV recording for testing

### 6. Setup Recording Software (Audacity)
1. Open Audacity
2. Set recording device to: BlackHole 2ch
3. Set recording to: Stereo
4. Click record
5. Verify levels appear

---

## Troubleshooting

### No Audio in Audacity

**Problem:** Audacity doesn't receive signal from BlackHole
**Solutions:**
1. Create Aggregate Device (see Setup Instructions)
2. Open another audio app (MainStage, GarageBand) with BlackHole as input to "activate" it
3. Use Multi-Output Device instead of BlackHole alone
4. Check Audacity's recording device dropdown
5. Enable "Software Playthrough" in Audacity preferences

### Levels Don't Match H8 Display

**Problem:** Script shows different dB values than H8 meters
**Solution:**
- Adjust `input_gain_db` to match
- If H8 shows -20 dB but script shows -40 dB, add +20 dB input gain
- Adjust threshold accordingly

### Audio Distortion/Clipping

**Problem:** Recorded audio is distorted
**Solutions:**
1. Reduce `output_gain_db`
2. Reduce gain on H8 hardware
3. Check for clipping indicator on H8
4. Output is auto-clipped at ±1.0, but may still be too hot

### Channels Not Switching

**Problem:** Script stuck on one channel
**Solutions:**
1. Reduce `threshold_db` (make it more sensitive)
2. Increase `input_gain_db` if levels are too low
3. Reduce `channel_hold_time` for faster switching
4. Check priority channel settings

### Script Crashes/Errors

**Problem:** Memory allocation error
**Solution:** Script already uses blocking I/O instead of callbacks

**Problem:** Device not found
**Solutions:**
1. Run script, check device list carefully
2. Make sure H8 is powered on and in Interface mode
3. Try unplugging and reconnecting H8
4. Check USB cable quality

---

## Usage Examples

### Example 1: Podcast Recording
**Scenario:** 3 hosts on channels 1, 2, 3. Whoever speaks becomes active.

**Settings:**
```
Priority channels: [1, 2, 3]
Threshold: -35 dB
Hold time: 0.3 seconds
Channel 3 exclusive: False
```

### Example 2: Main + Backup Mic
**Scenario:** Channel 1 is main mic, others are backup. Always prefer channel 1.

**Settings:**
```
Priority channels: [1]
Threshold: -40 dB
Hold time: 0.5 seconds
Channel 3 exclusive: False
```

### Example 3: Presenter + Audience
**Scenario:** Channel 3 is presenter (priority). When presenter speaks, ignore audience mics except channel 1 (host).

**Settings:**
```
Priority channels: [3, 1]
Threshold: -35 dB
Hold time: 0.8 seconds
Channel 3 exclusive: True  ← Key setting
```

---

## Code Structure

### Main Classes

**AudioChannelRouter**
- Main class handling all audio processing
- Methods:
  - `__init__()`: Initialize parameters
  - `get_channel_levels()`: Calculate RMS for all channels
  - `select_active_channel()`: Apply selection logic
  - `process_audio_blocking()`: Main audio loop
  - `start()`: Start routing
  - `stop()`: Clean shutdown
  - `start_recording()`: Begin WAV file recording
  - `stop_recording()`: Save and close WAV file
  - `list_devices()`: Display available audio devices

### Key Methods Explained

**get_channel_levels(audio_data)**
```python
# Purpose: Calculate dB level for each channel
# Input: Multi-channel audio buffer (numpy array)
# Output: List of dB values (one per channel)
# Process:
#   1. Apply input gain
#   2. Calculate RMS for each channel
#   3. Convert to dB
#   4. Apply 5-sample smoothing
#   5. Return smoothed levels
```

**select_active_channel(levels)**
```python
# Purpose: Choose which channel should be active
# Input: List of channel levels in dB
# Output: Channel index (0-9) or None
# Process:
#   1. Check hold time
#   2. Apply special rules (e.g., Ch3 exclusive)
#   3. Check priority channels
#   4. Find highest level
#   5. Return channel or None
```

**process_audio_blocking()**
```python
# Purpose: Main audio processing loop
# Process:
#   1. Read input buffer
#   2. Apply input gain
#   3. Calculate levels
#   4. Select active channel
#   5. Apply output gain
#   6. Clip to ±1.0
#   7. Write to output
#   8. Record to WAV if enabled
#   9. Display status
#   10. Repeat
```

---

## Customization Guide

### Adding New Channel Selection Logic

To add custom logic (e.g., "if channel 5 active, mute channels 6-10"):

```python
# In select_active_channel() method, add before priority check:

# Custom rule: Channel 5 exclusive for channels 6-10
if levels[4] > self.threshold_db:  # Channel 5 (index 4)
    # Only allow channels 1-5
    active_channels = [(i, level) for i, level in enumerate(levels[:5]) 
                      if level > self.threshold_db]
    if active_channels:
        return max(active_channels, key=lambda x: x[1])[0]
```

### Changing Output Routing

To route different channels to L/R (not duplicate):

```python
# In process_audio_blocking(), replace:
outdata[:, 0] = channel_data  # Left
outdata[:, 1] = channel_data  # Right

# With:
outdata[:, 0] = indata_gained[:, active_ch]      # Left = active channel
outdata[:, 1] = indata_gained[:, (active_ch+1)]  # Right = next channel
```

### Adding More Outputs

To output multiple channels simultaneously:

```python
# Change OUTPUT_CHANNELS to desired number
self.OUTPUT_CHANNELS = 4  # For quad output

# In processing:
outdata[:, 0] = indata_gained[:, channel1]
outdata[:, 1] = indata_gained[:, channel2]
outdata[:, 2] = indata_gained[:, channel3]
outdata[:, 3] = indata_gained[:, channel4]
```

---

## Performance Considerations

### CPU Usage
- Block size affects latency vs. CPU trade-off
- Smaller blocks = lower latency, higher CPU
- 1024 samples @ 48kHz = ~21ms latency (acceptable)

### Memory Usage
- Minimal: Only stores last 5 level values per channel
- WAV recording stores all frames in memory before writing

### Optimization Tips
1. Increase CHUNK size if CPU usage is high
2. Disable WAV recording when not testing
3. Reduce level smoothing window if faster response needed

---

## API Reference

### Constructor Parameters
```python
router = AudioChannelRouter()
# All parameters set via instance variables after creation
```

### Public Methods

**list_devices()**
```python
router.list_devices()
# Returns: List of device info dicts
# Displays: All available audio devices with capabilities
```

**start(input_device_idx, output_device_idx)**
```python
router.start(0, 1)
# Parameters:
#   input_device_idx: Device number for input (Zoom H8)
#   output_device_idx: Device number for output (BlackHole)
# Returns: None (blocks until stopped)
```

**stop()**
```python
router.stop()
# Stops audio processing and closes streams
```

**start_recording(filename=None)**
```python
filename = router.start_recording()
# Parameters:
#   filename: Optional WAV filename (auto-generated if None)
# Returns: Filename of recording
```

**stop_recording()**
```python
router.stop_recording()
# Saves and closes WAV file
```

---

## Testing Procedures

### 1. Hardware Test
```
Goal: Verify H8 is connected and recognized
Steps:
  1. Run script
  2. Check device list for "ZOOM H8" with 10+ input channels
  3. Select H8 as input
  4. If not found: check USB connection, H8 mode, drivers
```

### 2. Channel Detection Test
```
Goal: Verify all channels can be detected
Steps:
  1. Enable WAV recording
  2. Set threshold to -50 dB (very sensitive)
  3. Speak into each mic one at a time
  4. Verify channel number changes for each mic
  5. Stop and check WAV file has audio
```

### 3. Level Calibration Test
```
Goal: Match displayed levels to H8 meters
Steps:
  1. Set known level on H8 (e.g., -20 dB)
  2. Compare to script display
  3. Adjust input_gain_db until levels match
  4. This is your calibration offset
```

### 4. Priority Logic Test
```
Goal: Verify priority channels work correctly
Steps:
  1. Set priority_channels = [1, 3]
  2. Have audio on channels 2, 3, 4 simultaneously
  3. Verify channel 3 is selected (higher priority)
  4. Stop channel 3
  5. Verify switches to channel 2 or 4
```

### 5. Exclusive Mode Test
```
Goal: Verify channel 3 exclusive logic
Steps:
  1. Enable channel_3_exclusive = True
  2. Have audio on channels 2, 3, 4
  3. Verify only channel 3 is selected
  4. Add audio on channel 1
  5. Verify can switch between channel 1 and 3 only
```

### 6. Output Test
```
Goal: Verify audio reaches recording software
Steps:
  1. Start router with BlackHole output
  2. Open Audacity, set input to BlackHole
  3. Monitor input levels
  4. Speak into mic
  5. Verify levels appear in Audacity
  6. Record and playback to confirm audio quality
```

---

## Future Enhancements

### Potential Features
1. **GUI Interface**: Visual level meters, click to configure
2. **MIDI Control**: Use MIDI controller to override channel selection
3. **Auto-Ducking**: Lower background channels when priority channel active
4. **Mix Mode**: Blend multiple channels instead of selecting one
5. **Presets**: Save/load different configurations
6. **Network Control**: Web interface for remote control
7. **VST Plugin**: Run as plugin in DAW
8. **Multi-Track Recording**: Record all channels + selected output
9. **Noise Gate**: Per-channel noise gates before selection
10. **Compression**: Apply dynamics to output

### Technical Debt
- Add unit tests for channel selection logic
- Create configuration file format (JSON/YAML)
- Add logging system for debugging
- Implement graceful error recovery
- Add latency compensation
- Support variable sample rates
- Add buffer underrun handling

---

## Frequently Asked Questions

**Q: Can I use more than 10 input channels?**
A: Yes, change `INPUT_CHANNELS` to match your interface. The H8 supports 12.

**Q: Can I route different channels to L and R outputs?**
A: Yes, modify the output routing in `process_audio_blocking()`. See Customization Guide.

**Q: Why is there a delay between speaking and channel switching?**
A: This is the `channel_hold_time`. Reduce it for faster switching, but it may become unstable.

**Q: Can I use this with other audio interfaces?**
A: Yes, any multi-channel interface works. Just select the correct device.

**Q: Does this work on Windows/Linux?**
A: Yes, but you need different virtual audio drivers (VB-Cable for Windows, PulseAudio for Linux).

**Q: Can I run multiple instances simultaneously?**
A: Not with the same audio interface, but you could split devices.

**Q: How do I add automatic mixing instead of selection?**
A: Modify `process_audio_blocking()` to sum multiple channels instead of selecting one.

**Q: Can this introduce latency?**
A: Yes, ~21ms with default settings. Reduce CHUNK size to reduce latency.

**Q: What if I want to record all channels separately?**
A: Use the H8's built-in recording or modify script to write multi-channel WAV.

---

## Support and Resources

### Documentation
- sounddevice: https://python-sounddevice.readthedocs.io/
- NumPy: https://numpy.org/doc/
- WAV format: https://docs.python.org/3/library/wave.html

### Community
- Report issues: [Create GitHub issue with full error message]
- Feature requests: [Describe use case and desired behavior]

### Version History
- v1.0: Initial release with basic channel selection
- v1.1: Added gain controls and WAV recording
- v1.2: Fixed blocking I/O for compatibility

---

## License and Credits

This project uses:
- `sounddevice` library (MIT License)
- `numpy` (BSD License)
- Python standard library

Created for multi-channel audio routing and intelligent channel selection.

---

## Quick Reference Card

### Command Line
```bash
# Install dependencies
pip install sounddevice numpy

# Run router
python audio_router.py

# Stop router
Ctrl+C
```

### Configuration Quick Values
```
Sensitive: threshold=-50, hold_time=0.2
Normal:    threshold=-40, hold_time=0.5
Stable:    threshold=-30, hold_time=1.0

Quiet input:  input_gain=+20
Normal input: input_gain=0
Hot input:    input_gain=-10

Quiet output:  output_gain=+12
Normal output: output_gain=0
Hot output:    output_gain=-6
```

### Device Setup Checklist
- [ ] Virtual audio device installed
- [ ] Audio interface connected and recognized
- [ ] Sample rates match (48000 Hz recommended)
- [ ] Aggregate device created (macOS)
- [ ] Recording software configured
- [ ] Test recording successful

---

*End of Documentation*