"""
Terminal UI — a simple text-based interface for the Improvised TRPG Agent.
Uses the `rich` library for styled console output and collapsible debug panels.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from models import SessionContract
from orchestrator import Orchestrator, TurnResult


console = Console()


# ── display helpers ──────────────────────────────────────────────────────

def show_banner() -> None:
    console.print(
        Panel(
            "[bold cyan]Improvised TRPG Agent[/bold cyan]\n"
            "[dim]空白世界，即兴创作。键入你的行动，世界随之展开。[/dim]\n"
            "[dim]输入 /help 查看可用命令[/dim]",
            border_style="cyan",
        )
    )


def show_narrative(text: str) -> None:
    console.print()
    console.print(Panel(Markdown(text), title="[bold green]叙事[/bold green]", border_style="green"))


def show_system_messages(messages: list[str]) -> None:
    if not messages:
        return
    console.print()
    for msg in messages:
        console.print(f"  [yellow]{msg}[/yellow]")


def show_debug_panel(result: TurnResult) -> None:
    console.print()
    console.print("[bold magenta]─── Debug Info ───[/bold magenta]")

    if result.validation_errors:
        console.print("[red]Validation Errors:[/red]")
        for err in result.validation_errors:
            console.print(f"  [red]✗ {err}[/red]")

    if result.validation_warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in result.validation_warnings:
            console.print(f"  [yellow]⚠ {w}[/yellow]")

    if result.patch_report and result.patch_report.patched:
        console.print(f"[yellow]Narrative patched — smuggled names: {result.patch_report.smuggled_names}[/yellow]")

    if result.events:
        table = Table(title="Events This Turn", show_lines=True)
        table.add_column("Type", style="cyan")
        table.add_column("Visibility")
        table.add_column("Payload (summary)")
        for ev in result.events:
            payload_str = _summarize_payload(ev.payload)
            vis_style = "dim" if ev.visibility.value == "gm_only" else ""
            table.add_row(ev.type, f"[{vis_style}]{ev.visibility.value}[/{vis_style}]", payload_str)
        console.print(table)


def _summarize_payload(payload: dict[str, Any], max_len: int = 80) -> str:
    parts: list[str] = []
    for k, v in payload.items():
        s = f"{k}={v}"
        parts.append(s if len(s) <= 40 else s[:37] + "...")
    text = ", ".join(parts)
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def prompt_input() -> str:
    try:
        console.print()
        text = console.input("[bold blue]> 你的行动: [/bold blue]")
        return text.strip()
    except (EOFError, KeyboardInterrupt):
        return "/quit"


# ── session zero ─────────────────────────────────────────────────────────

def session_zero() -> SessionContract:
    console.print("\n[bold cyan]── Session Zero：世界设定 ──[/bold cyan]")
    genre = console.input("[cyan]题材[/cyan] (奇幻/赛博/克苏鲁/武侠/都市怪谈/...): ").strip() or "奇幻"
    style = console.input("[cyan]风格[/cyan] (轻松/严肃/黑色/喜剧/高危险/...): ").strip() or "严肃"
    boundaries_raw = console.input("[cyan]边界[/cyan] (不想出现的内容，逗号分隔，可留空): ").strip()
    boundaries = [b.strip() for b in boundaries_raw.split(",") if b.strip()] if boundaries_raw else []
    return SessionContract(genre=genre, style=style, boundaries=boundaries)


# ── slash commands ───────────────────────────────────────────────────────

def handle_command(cmd: str, orch: Orchestrator, debug_mode: bool) -> tuple[bool, bool]:
    """Returns (should_continue, debug_mode)."""
    if cmd == "/quit":
        console.print("[dim]再见！[/dim]")
        return False, debug_mode
    if cmd == "/help":
        console.print(
            Panel(
                "/debug  — 切换 debug 面板\n"
                "/state  — 查看当前实体列表\n"
                "/facts  — 查看已确立的事实\n"
                "/save   — 手动保存快照\n"
                "/quit   — 退出游戏",
                title="可用命令",
            )
        )
        return True, debug_mode
    if cmd == "/debug":
        debug_mode = not debug_mode
        state_text = "开启" if debug_mode else "关闭"
        console.print(f"[magenta]Debug 面板已{state_text}[/magenta]")
        return True, debug_mode
    if cmd == "/state":
        _show_entities(orch)
        return True, debug_mode
    if cmd == "/facts":
        _show_facts(orch)
        return True, debug_mode
    if cmd == "/save":
        orch.event_log.save_snapshot(orch.current_turn, orch.store.export_state())
        console.print("[green]快照已保存[/green]")
        return True, debug_mode
    console.print(f"[red]未知命令: {cmd}[/red]")
    return True, debug_mode


def _show_entities(orch: Orchestrator) -> None:
    entities = orch.store.list_entities()
    if not entities:
        console.print("[dim]世界中还没有任何实体。[/dim]")
        return
    table = Table(title="World Entities")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Tags")
    for e in entities:
        table.add_row(e.id[:8] + "...", e.type.value, e.display_name, ", ".join(e.tags))
    console.print(table)


def _show_facts(orch: Orchestrator) -> None:
    facts = orch.store.get_canon_facts()
    if not facts:
        console.print("[dim]还没有任何已确立的事实。[/dim]")
        return
    table = Table(title="Canon Facts")
    table.add_column("Subject", style="cyan")
    table.add_column("Predicate")
    table.add_column("Object")
    table.add_column("Status", style="dim")
    for f in facts:
        table.add_row(f.subject_id[:12], f.predicate, f.object, f.status.value)
    console.print(table)


# ── main loop ────────────────────────────────────────────────────────────

def main() -> None:
    os.makedirs("data", exist_ok=True)
    show_banner()

    contract = session_zero()
    orch = Orchestrator(session_contract=contract)
    orch.bootstrap_session(contract.genre, contract.style, contract.boundaries)

    console.print(f"\n[green]世界已创建！题材：{contract.genre}，风格：{contract.style}[/green]")
    console.print("[dim]输入「开始」来启动第一幕，或直接描述你想做的事。[/dim]\n")

    debug_mode = False

    while True:
        player_input = prompt_input()
        if not player_input:
            continue

        if player_input.startswith("/"):
            should_continue, debug_mode = handle_command(player_input, orch, debug_mode)
            if not should_continue:
                break
            continue

        try:
            result = orch.run_turn(player_input)
        except Exception as exc:
            console.print(f"[red]系统错误: {exc}[/red]")
            continue

        show_narrative(result.narrative)
        show_system_messages(result.system_messages)

        if debug_mode:
            show_debug_panel(result)


if __name__ == "__main__":
    main()
