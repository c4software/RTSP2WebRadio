# Babyphone RTSP Audio Stream

This project provides a "baby phone" solution using an RTSP camera. It captures the audio from the camera's video stream and exposes it as a live MP3 stream, similar to a web radio.

## Features

- Converts RTSP video stream to MP3 audio stream in real-time
- Accessible via HTTP (`/stream.mp3`)
- Multiple clients supported
- Efficient resource management: FFmpeg starts/stops based on client connections
- Status endpoint (`/status`) for monitoring

## Use Case

Monitor your baby using any RTSP-compatible camera. Listen to the audio stream from anywhere via a simple web browser or audio player.

## Quick Start

1. **Clone the repository**
2. **Configure your RTSP URL**  
   Set the `RTSP_URL` in the `.env` file.
3. **Start the service**  
   Use Docker Compose or run `app.py` directly.

## Example `.env`

```env
# Generic RTSP camera
RTSP_URL=rtsp://username:password@camera_ip/stream_path

# Reolink
RTSP_URL=rtsp://admin:yourpassword@192.168.1.100:554/h264Preview_01_main

# Hikvision
RTSP_URL=rtsp://admin:yourpassword@192.168.1.101:554/Streaming/Channels/101/

# Dahua
RTSP_URL=rtsp://admin:yourpassword@192.168.1.102:554/cam/realmonitor?channel=1&subtype=0
```

## Endpoints

- `/stream.mp3` — Live MP3 audio stream
- `/status` — Server and stream status (JSON)

## Run with Docker Compose

```bash
docker-compose up
```

## License

MIT

## Author

Valentin Brosseau
