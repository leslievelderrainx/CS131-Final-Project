import os
os.environ["TFHUB_CACHE_DIR"] = os.path.expanduser("~/.cache/tfhub")

import wave
import json
import time
import math
import struct
import requests
from pathlib import Path

import tensorflow as tf
import tensorflow_hub as hub
import numpy as np
import csv
from scipy import signal
from scipy.io import wavfile
import sys
import scipy

WAV_PATH = "received_audio/recording.wave"   # change if needed
DEVICE_ID = "jetson-01"

CLOUD_URL = "http://35.232.171.192:5000/metrics"  # <-- change this

def pcm16_rms_db(frames: bytes, channels: int) -> float:
    """Compute RMS and convert to 'dBFS' for 16-bit PCM (relative to full-scale)."""
    if not frames:
        return float("-inf")

    # 16-bit signed little-endian samples
    num_samples = len(frames) // 2
    samples = struct.unpack("<" + "h" * num_samples, frames)

    # If stereo, average the channels (simple)
    if channels > 1:
        samples = [sum(samples[i:i+channels]) / channels for i in range(0, len(samples), channels)]

    mean_sq = sum((s * s) for s in samples) / len(samples)
    rms = math.sqrt(mean_sq)

    # Full scale for int16 is 32768
    if rms == 0:
        return float("-inf")

    dbfs = 20 * math.log10(rms / 32768.0)
    return dbfs

def analyze_wav(path: str, window_sec: float = 1.0):
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()

        if sampwidth != 2:
            raise ValueError(f"Expected 16-bit PCM (sampwidth=2), got sampwidth={sampwidth}")

        total_sec = nframes / rate
        frames_per_window = int(rate * window_sec)

        db_series = []
        wf.rewind()

        while True:
            frames = wf.readframes(frames_per_window)
            if not frames:
                break
            db_series.append(pcm16_rms_db(frames, channels))

    # Filter -inf if you want cleaner stats
    finite = [x for x in db_series if x != float("-inf")]
    avg_db = sum(finite) / len(finite) if finite else float("-inf")

    return {
        "device_id": DEVICE_ID,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wav_filename": Path(path).name,
        "duration_s": round(total_sec, 2),
        "avg_dbfs": avg_db,
        "max_dbfs": max(finite) if finite else float("-inf"),
        "min_dbfs": min(finite) if finite else float("-inf"),
        "dbfs_series_1s": db_series,
        "sample_rate": rate,
        "channels": channels,
    }

def load_model():
   model = hub.load('https://tfhub.dev/google/yamnet/1')
   return model

# Find the name of the class with the top score when mean-aggregated across frames.
def class_names_from_csv(class_map_csv_text):
  """Returns list of class names corresponding to score vector."""
  class_names = []
  with tf.io.gfile.GFile(class_map_csv_text) as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
      class_names.append(row['display_name'])

  return class_names

def ensure_sample_rate(original_sample_rate, waveform,
                       desired_sample_rate=16000):
  """Resample waveform if required."""
  if original_sample_rate != desired_sample_rate:
    desired_length = int(round(float(len(waveform)) /
                               original_sample_rate * desired_sample_rate))
    waveform = scipy.signal.resample(waveform, desired_length)
  return desired_sample_rate, waveform

def execute_model(waveform, model, class_names):
    scores, embeddings, spectrogram = model(waveform)
    scores_np = scores.numpy()

    mean_scores = scores_np.mean(axis=0)
    top_index = mean_scores.argmax()
    
    inferred_class = class_names[top_index]
    confidence = float(mean_scores[top_index])
    
    return inferred_class, confidence

def send_to_cloud(payload: dict):
    print("Sending to:", CLOUD_URL)
    r = requests.post(CLOUD_URL, json=payload, timeout=10)
    print("Cloud response:", r.status_code, r.text)

if __name__ == "__main__":
    # accept filepath from fog_server call
    wav_path = sys.argv[1] if len(sys.argv) > 1 else "received_audio/recording.wave"

    payload = analyze_wav(wav_path, window_sec=1.0)

    model = load_model()
    class_map_path = model.class_map_path().numpy()
    class_names = class_names_from_csv(class_map_path)

    sample_rate, waveform = wavfile.read(wav_path)

    # stereo -> mono
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=1)

    # normalize properly
    waveform = waveform.astype(np.float32) / np.iinfo(np.int16).max

    # resample to 16k
    sample_rate, waveform = ensure_sample_rate(sample_rate, waveform, 16000)

    inferred, confidence = execute_model(waveform, model, class_names)
    print(f"The main sound is: {inferred}")

    # include ML result in payload
    payload["yamnet_label"] = inferred
    payload["yamnet_confidence"] = confidence

    print(json.dumps(payload, indent=2))
    send_to_cloud(payload)