# Base image légère
FROM python:3.12-alpine

# Installer FFmpeg (minimal) et dépendances système
RUN apk add --no-cache ffmpeg

# Créer un répertoire de travail
WORKDIR /app

# Installer Flask (seul paquet Python nécessaire)
RUN pip install --no-cache-dir flask

# Copier le script Python (nommé app.py)
COPY app.py .

# Exposer le port
EXPOSE 5000

# Commande de démarrage : lance le serveur Flask
CMD ["python", "app.py"]
