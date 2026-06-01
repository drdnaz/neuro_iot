import asyncio
import os
import sys
import struct
from pathlib import Path

# Add voice server directory to path
server_dir = Path(r"c:\Users\drdna\iot_project2\llm-voice-server\llm-voice-server")
sys.path.insert(0, str(server_dir))

import edge_tts

async def test_tts():
    text = "Hello! The system is fully operational."
    voice = "en-US-GuyNeural"
    rate = 22050
    
    print(f"Synthesizing: '{text}' using {voice} at {rate}Hz...")
    
    communicate = edge_tts.Communicate(text, voice)
    mp3_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_chunks.append(chunk["data"])
            
    if not mp3_chunks:
        print("Error: No audio chunks received from edge-tts!")
        return
        
    mp3_data = b"".join(mp3_chunks)
    print(f"MP3 data generated: {len(mp3_data)} bytes")
    
    # Convert using ffmpeg
    import subprocess
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", "pipe:0",
         "-map_metadata", "-1", "-fflags", "+bitexact",
         "-ar", str(rate), "-ac", "1", "-acodec", "pcm_s16le", "-filter:a", "volume=5.0", "-f", "wav", "pipe:1"],
        input=mp3_data,
        capture_output=True,
        timeout=30,
    )
    
    if proc.returncode != 0:
        print(f"FFmpeg error: {proc.stderr.decode('utf-8', 'ignore')}")
        return
        
    wav_bytes = proc.stdout
    print(f"WAV data generated: {len(wav_bytes)} bytes")
    
    if len(wav_bytes) <= 44:
        print("Error: WAV too short or empty!")
        return
        
    raw_pcm = wav_bytes[44:]
    print(f"Raw PCM: {len(raw_pcm)} bytes")
    
    samples = struct.unpack(f"<{len(raw_pcm)//2}h", raw_pcm)
    avg_amp = sum(abs(s) for s in samples) / len(samples) if samples else 0
    print(f"Average Amplitude: {avg_amp:.2f}")
    
    # Check if silent
    if avg_amp < 10:
        print("WARNING: Audio is silent (all zeros)!")
    else:
        print("Success: Audio contains real speech waveform data!")

if __name__ == "__main__":
    asyncio.run(test_tts())
