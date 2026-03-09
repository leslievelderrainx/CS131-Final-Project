import wave
import pyaudio
import requests 
import os

DEVICE_INDEX = 11
RATE = 44100
CHUNK = 4096
CHANNELS = 1
FORMAT = pyaudio.paInt16
SECONDS = 60
FILENAME = "recording.wave"

FOG_IP = "10.13.189.119"
FOG_PORT = 8000
FOG_URL = f"http://{FOG_IP}:{FOG_PORT}/upload"

def record_audio(): 
    p = pyaudio.PyAudio()

    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=RATE,
        input=True, 
        input_device_index=DEVICE_INDEX, 
        frames_per_buffer=CHUNK,
    )

    print(f"Recording {SECONDS} seconds...") 

    frames = []
    num_chunks = int(RATE/CHUNK * SECONDS)

    for _ in range(num_chunks):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)

    stream.stop_stream()
    stream.close()
    p.terminate()

    print("Recording complete.") 

    wf = wave.open(FILENAME, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()

    print (f"Saved file: {FILENAME}")


def send_to_fog(): 
    print("Sending file to fog node...")

    with open(FILENAME, "rb") as f:
        response = requests.post(
            FOG_URL,
            data=f.read(),
            headers={"X-Filename": FILENAME}
        )

    print("Fog response:", response.status_code, response.text)


if __name__ == "__main__":
    record_audio()
    send_to_fog()

