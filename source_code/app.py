"""
Flask Web UI for the Improvised TRPG Agent.
Provides 开始 / 载入 / 退出 and a split-pane game view.
"""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify, render_template, request

from models import SessionContract
from orchestrator import Orchestrator
from save_utils import ensure_save_dir, get_db_path, list_saves, save_exists, sanitize_save_name

app = Flask(__name__, template_folder="templates", static_folder="static")
orchestrators: dict[str, Orchestrator] = {}


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/api/saves")
def api_saves() -> Any:
    return jsonify(list_saves())


@app.route("/api/start", methods=["POST"])
def api_start() -> Any:
    data = request.get_json() or {}
    save_name = sanitize_save_name(data.get("save_name", "default"))
    genre = data.get("genre", "奇幻").strip() or "奇幻"
    style = data.get("style", "严肃").strip() or "严肃"
    boundaries = data.get("boundaries", [])
    if isinstance(boundaries, str):
        boundaries = [b.strip() for b in boundaries.split(",") if b.strip()]

    db_path = ensure_save_dir(save_name)
    contract = SessionContract(genre=genre, style=style, boundaries=boundaries)
    orch = Orchestrator(db_path=db_path, session_contract=contract)
    orch.bootstrap_session(genre, style, boundaries)
    orchestrators[save_name] = orch
    return jsonify({"ok": True, "save_name": save_name})


@app.route("/api/load", methods=["POST"])
def api_load() -> Any:
    data = request.get_json() or {}
    save_name = data.get("save_name", "").strip()
    if not save_name or save_name not in list_saves():
        return jsonify({"error": "存档不存在"}), 404

    db_path = get_db_path(save_name)
    orch = Orchestrator(db_path=db_path)
    orchestrators[save_name] = orch

    history = [
        {"role": role, "text": text}
        for _, role, text in orch.event_log.get_transcript_events(window=50)
    ]
    contract = orch.event_log.get_session_contract() or {}

    return jsonify({
        "ok": True,
        "save_name": save_name,
        "history": history,
        "session": {
            "genre": contract.get("genre", ""),
            "style": contract.get("style", ""),
            "turn": orch.current_turn,
        },
    })


@app.route("/api/turn", methods=["POST"])
def api_turn() -> Any:
    data = request.get_json() or {}
    save_name = data.get("save_name", "")
    player_input = (data.get("input") or "").strip()

    if save_name not in orchestrators:
        return jsonify({"error": "请先开始或载入游戏"}), 400
    if not player_input:
        return jsonify({"error": "请输入行动"}), 400

    orch = orchestrators[save_name]
    try:
        result = orch.run_turn(player_input)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({
        "narrative": result.narrative,
        "system_messages": result.system_messages,
        "turn": orch.current_turn,
        "events_count": len(result.events),
    })


@app.route("/api/state", methods=["POST"])
def api_state() -> Any:
    data = request.get_json() or {}
    save_name = data.get("save_name", "")
    if save_name not in orchestrators:
        return jsonify({"error": "请先开始或载入游戏"}), 400
    orch = orchestrators[save_name]
    entities = [
        {"id": e.id[:8], "type": e.type.value, "name": e.display_name, "tags": e.tags}
        for e in orch.store.list_entities()
    ]
    facts = [
        {"subject": f.subject_id[:12], "predicate": f.predicate, "object": f.object, "status": f.status.value}
        for f in orch.store.get_canon_facts()
    ]
    return jsonify({"entities": entities, "facts": facts})


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    port = int(os.getenv("FLASK_PORT", "5001"))
    print(f"RPG Heaven Web UI: http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
