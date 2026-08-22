import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

import bgg
import pricing

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/bgg/search")
def bgg_search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Parametro 'q' mancante"}), 400
    try:
        return jsonify(bgg.search_games(query))
    except bgg.BGGError as e:
        return jsonify({"error": str(e)}), 502


@app.get("/api/bgg/game/<game_id>")
def bgg_game(game_id):
    try:
        return jsonify(bgg.get_game(game_id))
    except bgg.BGGError as e:
        return jsonify({"error": str(e)}), 502


@app.get("/api/price")
def price():
    title = request.args.get("title", "").strip()
    if not title:
        return jsonify({"error": "Parametro 'title' mancante"}), 400
    return jsonify(pricing.search_price(title))


if __name__ == "__main__":
    if not os.environ.get("BGG_API_TOKEN"):
        print("ATTENZIONE: BGG_API_TOKEN non impostato (vedi .env.example)")
    app.run(debug=True, port=5000)
