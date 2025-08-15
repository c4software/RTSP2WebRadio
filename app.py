import subprocess
import os
import logging
import threading
import queue
import time
from collections import deque
from typing import Deque, Optional, Generator
from flask import Flask, Response, stream_with_context, request

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)

RTSP_URL = os.getenv('RTSP_URL')
if not RTSP_URL:
    logging.error("La variable d'environnement RTSP_URL doit être définie.")
    raise ValueError("RTSP_URL n'est pas configurée.")

SERVER_PORT = 5000
SHUTDOWN_DELAY = 10.0
# Le buffer initial envoyé à la connexion pour un démarrage rapide
INITIAL_BUFFER_SECONDS = 5
BYTES_PER_SECOND = 4 * 1024  # Estimation pour 32kbit/s (32000/8)
INITIAL_BUFFER_MAX_CHUNKS = int(INITIAL_BUFFER_SECONDS * BYTES_PER_SECOND / 1024)
# Timeout pour la file d'attente client (évite les blocages)
CLIENT_QUEUE_TIMEOUT = 1.0


class ClientConnection:
    """Représente une connexion client avec son ID et sa file d'attente."""
    _next_id = 1
    _id_lock = threading.Lock()
    
    def __init__(self):
        with ClientConnection._id_lock:
            self.id = ClientConnection._next_id
            ClientConnection._next_id += 1
        self.queue = queue.Queue()
        self.connected = True
        self.last_activity = time.time()
    
    def disconnect(self):
        """Marque la connexion comme déconnectée."""
        self.connected = False
    
    def put_data(self, data):
        """Met des données dans la file d'attente du client."""
        if not self.connected:
            return False
        try:
            self.queue.put(data, timeout=0.1)
            self.last_activity = time.time()
            return True
        except queue.Full:
            logging.warning(f"File d'attente pleine pour le client {self.id}, déconnexion")
            self.connected = False
            return False
    
    def get_data(self, timeout=CLIENT_QUEUE_TIMEOUT):
        """Récupère des données de la file d'attente."""
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None


class AudioManager:
    """
    Classe centrale qui gère le processus FFmpeg et distribue le flux audio
    aux clients connectés via des files d'attente individuelles.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._ffmpeg_process: Optional[subprocess.Popen] = None
        self._ffmpeg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._shutdown_timer: Optional[threading.Timer] = None
        self._clients: dict[int, ClientConnection] = {}
        self._recent_chunks: Deque[bytes] = deque(maxlen=INITIAL_BUFFER_MAX_CHUNKS)
        self._cleanup_thread = None
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Démarre le thread de nettoyage des connexions inactives."""
        def cleanup_loop():
            while True:
                time.sleep(5)
                self._cleanup_inactive_clients()
        
        self._cleanup_thread = threading.Thread(target=cleanup_loop, name="ClientCleanup", daemon=True)
        self._cleanup_thread.start()

    def _cleanup_inactive_clients(self):
        """Nettoie les clients inactifs ou déconnectés."""
        current_time = time.time()
        clients_to_remove = []
        
        with self._lock:
            for client_id, client in self._clients.items():
                if not client.connected or (current_time - client.last_activity) > 30:
                    clients_to_remove.append(client_id)
            
            for client_id in clients_to_remove:
                if client_id in self._clients:
                    logging.info(f"Nettoyage du client inactif {client_id}")
                    del self._clients[client_id]
            
            if len(clients_to_remove) > 0:
                self._check_shutdown_ffmpeg()

    def add_client(self) -> ClientConnection:
        """Enregistre un nouveau client, lui crée une file d'attente et démarre FFmpeg si besoin."""
        client = ClientConnection()
        
        with self._lock:
            for chunk in self._recent_chunks:
                client.put_data(chunk)
            
            self._clients[client.id] = client
            client_count = len(self._clients)
            logging.info(f"Client {client.id} connecté (IP: {request.remote_addr}). Total : {client_count}.")

            if self._shutdown_timer:
                self._shutdown_timer.cancel()
                self._shutdown_timer = None
                logging.info("Arrêt de FFmpeg annulé - nouveau client connecté.")
            
            if client_count == 1 and not self._is_ffmpeg_running():
                self._start_ffmpeg()
        
        return client

    def remove_client(self, client: ClientConnection):
        """Retire un client et planifie l'arrêt de FFmpeg si c'est le dernier."""
        with self._lock:
            client.disconnect()
            if client.id in self._clients:
                del self._clients[client.id]
                client_count = len(self._clients)
                logging.info(f"Client {client.id} déconnecté. Restants : {client_count}.")
                
                self._check_shutdown_ffmpeg()

    def _check_shutdown_ffmpeg(self):
        """Vérifie si FFmpeg doit être arrêté (appelé avec le verrou acquis)."""
        client_count = len(self._clients)
        
        if client_count == 0 and self._is_ffmpeg_running():
            if not self._shutdown_timer:
                logging.info(f"Plus de clients. Arrêt de FFmpeg programmé dans {SHUTDOWN_DELAY} secondes.")
                self._shutdown_timer = threading.Timer(SHUTDOWN_DELAY, self._stop_ffmpeg)
                self._shutdown_timer.start()

    def get_client_count(self) -> int:
        """Retourne le nombre de clients connectés."""
        with self._lock:
            return len(self._clients)

    def _is_ffmpeg_running(self) -> bool:
        return (self._ffmpeg_process is not None and 
                self._ffmpeg_process.poll() is None and
                self._ffmpeg_thread is not None and 
                self._ffmpeg_thread.is_alive())

    def _start_ffmpeg(self):
        if self._is_ffmpeg_running():
            logging.warning("FFmpeg est déjà en cours d'exécution.")
            return
            
        self._stop_event.clear()
        logging.info("Démarrage du processus FFmpeg...")
        ffmpeg_command = [
            "ffmpeg", "-rtsp_transport", "tcp", "-i", RTSP_URL,
            "-vn", "-acodec", "libmp3lame", "-b:a", "32k",
            "-ar", "22050", "-ac", "1", "-f", "mp3",
            "-af", "aresample=async=1:first_pts=0", "-fflags", "+genpts",
            "-loglevel", "error", "-reconnect", "1", 
            "-reconnect_streamed", "1", "-reconnect_delay_max", "2",
            "pipe:1"
        ]
        try:
            self._ffmpeg_process = subprocess.Popen(
                ffmpeg_command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                bufsize=0 
            )
            self._ffmpeg_thread = threading.Thread(target=self._read_and_distribute, name="FFmpegReader")
            self._ffmpeg_thread.daemon = True
            self._ffmpeg_thread.start()
            logging.info("FFmpeg démarré avec succès.")
        except FileNotFoundError:
            logging.error("Erreur: La commande 'ffmpeg' n'a pas été trouvée.")
            self._ffmpeg_process = None
        except Exception as e:
            logging.error(f"Erreur lors du lancement de FFmpeg : {e}")
            self._ffmpeg_process = None

    def _read_and_distribute(self):
        """Lit la sortie de FFmpeg et distribue les données à tous les clients."""
        logging.info("Le thread de lecture et distribution a démarré.")
        
        while not self._stop_event.is_set() and self._ffmpeg_process:
            try:
                data = self._ffmpeg_process.stdout.read(1024)
                if not data:
                    logging.warning("FFmpeg a cessé d'envoyer des données.")
                    break
                
                with self._lock:
                    self._recent_chunks.append(data)
                    disconnected_clients = []
                    for client_id, client in self._clients.items():
                        if not client.put_data(data):
                            disconnected_clients.append(client_id)
                    
                    for client_id in disconnected_clients:
                        if client_id in self._clients:
                            logging.info(f"Client {client_id} supprimé (file d'attente pleine)")
                            del self._clients[client_id]
                    
                    if disconnected_clients:
                        self._check_shutdown_ffmpeg()
                        
            except Exception as e:
                logging.error(f"Erreur lors de la lecture FFmpeg: {e}")
                break
        
        with self._lock:
            for client in self._clients.values():
                client.put_data(None)
        
        logging.info("Le thread de lecture et distribution s'est arrêté.")

    def _stop_ffmpeg(self):
        with self._lock:
            if len(self._clients) > 0:
                logging.info("Arrêt de FFmpeg annulé, des clients sont encore connectés.")
                self._shutdown_timer = None
                return
                
            logging.info("Arrêt de FFmpeg...")
            self._stop_event.set()
            
            if self._ffmpeg_process:
                try:
                    self._ffmpeg_process.terminate()
                    self._ffmpeg_process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    logging.warning("Terminaison forcée de FFmpeg.")
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.wait()
                except Exception as e:
                    logging.error(f"Erreur lors de l'arrêt de FFmpeg: {e}")
                finally:
                    self._ffmpeg_process = None
            
            if self._ffmpeg_thread:
                self._ffmpeg_thread.join(timeout=3.0)
                if self._ffmpeg_thread.is_alive():
                    logging.warning("Le thread FFmpeg ne s'est pas arrêté proprement.")
                self._ffmpeg_thread = None
            
            self._shutdown_timer = None
            logging.info("FFmpeg est complètement arrêté.")

app = Flask(__name__)
audio_manager = AudioManager()

@app.route('/stream.mp3')
def stream_mp3():
    """Route principale qui gère le streaming pour un client."""
    def generate_audio() -> Generator[bytes, None, None]:
        """Générateur qui lit depuis la file d'attente personnelle du client."""
        client = audio_manager.add_client()
        logging.info(f"Début du streaming pour le client {client.id}.")
        
        try:
            while client.connected:
                chunk = client.get_data()
                
                if chunk is None:
                    continue
                elif chunk == b'': 
                    break
                    
                yield chunk
                
        except GeneratorExit:
            logging.info(f"Le client {client.id} a fermé la connexion.")
        except Exception as e:
            logging.error(f"Erreur dans le streaming pour le client {client.id}: {e}")
        finally:
            audio_manager.remove_client(client)
            logging.info(f"Fin du streaming pour le client {client.id}.")

    return Response(stream_with_context(generate_audio()), mimetype='audio/mpeg')

@app.route('/status')
def status():
    """Route pour obtenir le statut du serveur."""
    client_count = audio_manager.get_client_count()
    ffmpeg_running = audio_manager._is_ffmpeg_running()
    
    return {
        'clients_connected': client_count,
        'ffmpeg_running': ffmpeg_running,
        'rtsp_url': RTSP_URL
    }, 200

if __name__ == '__main__':
    logging.info(f"Démarrage du serveur de streaming RTSP sur le port {SERVER_PORT}")
    logging.info(f"URL RTSP configurée: {RTSP_URL}")
    app.run(host='0.0.0.0', port=SERVER_PORT, threaded=True)
