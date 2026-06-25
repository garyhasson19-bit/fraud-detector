#!/bin/bash
# Lanceur FraudLens — Double-cliquez ou exécutez depuis le terminal

set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  FraudLens — Détection de fraude"
echo "=========================================="

# Vérifie que Python est installé
if ! command -v python3 &>/dev/null; then
    echo "ERREUR: Python 3 n'est pas installé."
    echo "Téléchargez-le sur https://www.python.org/downloads/"
    exit 1
fi

# Crée un environnement virtuel si nécessaire
if [ ! -d ".venv" ]; then
    echo "Création de l'environnement Python..."
    python3 -m venv .venv
fi

# Active l'environnement
source .venv/bin/activate

# Installe les dépendances
echo "Installation / vérification des dépendances..."
pip install -q -r requirements.txt

# Lance l'application
echo ""
echo "Démarrage de FraudLens..."
echo "L'application s'ouvrira automatiquement dans votre navigateur."
echo "(Si elle ne s'ouvre pas, allez sur http://localhost:8501)"
echo ""
streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
