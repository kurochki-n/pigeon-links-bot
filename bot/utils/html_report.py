from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile

from bot.database.models import (
    SmartLink,
    SmartLinkSource,
    SmartLinkVisit,
    SmartLinkVisitEvent,
    TelegramUser,
)


def _date(value: datetime | None) -> str:
    if not value:
        return "—"
    return (
        (value if value.tzinfo else value.replace(tzinfo=UTC))
        .astimezone(UTC)
        .strftime("%Y-%m-%d %H:%M UTC")
    )


def _summary(d: dict[str, int]) -> dict[str, str]:
    denominator = d["not_subscribed_initially"]
    return {
        **{k: str(v) for k, v in d.items()},
        "conversion": f"{(d['subscribed_after_redirect'] / denominator * 100 if denominator else 0):.2f}%",
    }


def create_report(
    summary: dict[str, int],
    links: Iterable[SmartLink],
    metrics: dict[int, dict[str, int]],
    channel: str,
    detail_link: SmartLink | None = None,
    visits: Iterable[SmartLinkVisit] = (),
    source_stats: Iterable[tuple[SmartLinkSource, dict[str, int]]] = (),
    users: Mapping[int, TelegramUser] | None = None,
    events: Iterable[SmartLinkVisitEvent] = (),
) -> Path:
    s = _summary(summary)
    users = users or {}
    rows = []
    for link in links:
        m = _summary(metrics.get(link.id, {k: 0 for k in summary}))
        material = "Telegram"
        downloads = "—"
        if link.content_type == "github_repository":
            repository = escape(f"{link.github_owner}/{link.github_repo}")
            material = f'<a href="{escape(link.github_url or "", quote=True)}">GitHub: {repository}</a>'
            downloads = str(link.downloads_count)
        elif link.content_type == "none":
            material = "Без материала"
        rows.append(
            f"<tr><td>{escape(link.name)}</td><td>{material}</td><td><code>{escape(link.slug)}</code></td><td>{_date(link.created_at)}</td><td>{m['total_visits']}</td><td>{m['unique_users']}</td><td>{m['already_subscribed']}</td><td>{m['not_subscribed_initially']}</td><td>{m['subscribed_after_redirect']}</td><td>{downloads}</td><td>{m['conversion']}</td></tr>"
        )
    source_rows = []
    for source, source_data in source_stats:
        source_summary = _summary(source_data)
        source_rows.append(
            f"<tr><td>{escape(source.name)}</td><td>{source.smart_link_id}</td><td>{source_summary['total_visits']}</td><td>{source_summary['unique_users']}</td><td>{source_summary['already_subscribed']}</td><td>{source_summary['not_subscribed_initially']}</td><td>{source_summary['subscribed_after_redirect']}</td><td>{source_summary['conversion']}</td></tr>"
        )
    sources_section = ""
    if source_rows:
        sources_section = f"""<section><h2>Источники трафика</h2><div class="table"><table><thead><tr>{"".join(f"<th>{x}</th>" for x in ["Источник", "Материал ID", "Переходов", "Уникальных", "Уже подписаны", "Не подписаны", "Подписались", "Конверсия"])}</tr></thead><tbody>{"".join(source_rows)}</tbody></table></div></section>"""
    detail = ""
    if detail_link:
        vr = []
        for v in visits:
            profile = users.get(v.telegram_user_id)
            username = profile.username if profile else v.username
            first_name = profile.first_name if profile else v.first_name
            last_name = profile.last_name if profile else v.last_name
            vr.append(
                f"<tr data-status={'after' if v.subscribed_after else ('initial' if v.was_subscribed else 'none')}><td>{v.telegram_user_id}</td><td>{escape('@' + username if username else '—')}</td><td>{escape(first_name or '—')}</td><td>{escape(last_name or '—')}</td><td>{_date(v.first_visit_at)}</td><td>{_date(v.last_visit_at)}</td><td>{v.visits_count}</td><td>{'Да' if v.was_subscribed else 'Нет'}</td><td>{'Да' if v.subscribed_after else 'Нет'}</td><td>{_date(v.subscribed_at)}</td></tr>"
            )
        event_rows = []
        for event in events:
            status = (
                "Подписался после"
                if event.subscribed_after
                else "Подписан"
                if event.was_subscribed
                else "Не подписан"
                if event.was_subscribed is False
                else "Неизвестно"
            )
            event_rows.append(
                f"<tr><td>{_date(event.visited_at)}</td><td>{escape(detail_link.name)}</td><td>{escape(event.source.name if event.source else 'Прямой')}</td><td>{status}</td></tr>"
            )
        events_section = (
            f"""<section><h2>История переходов</h2><div class="table"><table><thead><tr>{"".join(f"<th>{x}</th>" for x in ["Дата и время", "Материал", "Источник", "Статус подписки"])}</tr></thead><tbody>{"".join(event_rows)}</tbody></table></div></section>"""
            if event_rows
            else ""
        )
        detail = f"""<section><h2>Пользователи: {escape(detail_link.name)}</h2><div class="tools"><input id="query" placeholder="Поиск"><select id="filter"><option value="all">Все</option><option value="initial">Уже подписанные</option><option value="none">Не подписались</option><option value="after">Подписались после</option></select></div><div class="table"><table id="users"><thead><tr>{"".join(f"<th>{x}</th>" for x in ["Telegram ID", "Имя пользователя", "Имя", "Фамилия", "Первый переход", "Последний переход", "Переходов", "Был подписан", "Подписался после", "Дата подписки"])}</tr></thead><tbody>{"".join(vr)}</tbody></table></div></section>{events_section}"""
    cards = "".join(
        f"<article><small>{label}</small><strong>{s[key]}</strong></article>"
        for key, label in [
            ("total_visits", "Всего переходов"),
            ("unique_users", "Уникальных пользователей"),
            ("already_subscribed", "Уже были подписаны"),
            ("not_subscribed_initially", "Не были подписаны"),
            ("subscribed_after_redirect", "Подписались после перехода"),
            ("conversion", "Конверсия в подписку"),
        ]
    )
    html = f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Статистика Telegram-бота</title><style>:root{{--ink:#20201d;--muted:#74746c;--line:#e7e6e1;--paper:#f8f7f3}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 "Helvetica Neue",Arial,sans-serif}}main{{max-width:1240px;margin:auto;padding:48px 24px 72px}}header{{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:28px}}h1{{font:600 clamp(32px,5vw,56px)/1.05 Georgia,serif;letter-spacing:-.04em;margin:0 0 12px}}h2{{margin-top:48px}}p,small{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:10px}}article{{border:1px solid var(--line);border-radius:8px;background:#fff;padding:18px;min-height:105px}}article small{{display:block;text-transform:uppercase;font-size:10px;letter-spacing:.08em}}strong{{display:block;font-size:26px;margin-top:10px}}.table{{overflow:auto;border:1px solid var(--line);background:#fff;border-radius:8px}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th{{cursor:pointer;text-align:left;background:#f1f0eb}}th,td{{padding:12px;border-bottom:1px solid var(--line)}}.tools{{display:flex;gap:10px;margin:14px 0}}input,select{{padding:10px;border:1px solid var(--line);border-radius:5px;background:#fff}}code{{font-family:monospace}}@media(max-width:800px){{main{{padding:28px 14px}}.cards{{grid-template-columns:repeat(2,1fr)}}}}</style><main><header><h1>Статистика Telegram-бота</h1><p>Дата формирования: {_date(datetime.now(UTC))}<br>Канал: {escape(channel)}</p></header><section class="cards">{cards}</section><section><h2>Умные ссылки</h2><div class="tools"><input id="linkQuery" placeholder="Поиск по названию"></div><div class="table"><table id="links"><thead><tr>{"".join(f"<th>{x}</th>" for x in ["Название", "Материал", "Код ссылки", "Дата создания", "Всего переходов", "Разных людей", "Уже подписаны", "Не подписаны", "Подписались", "Получений ZIP", "Доля подписавшихся"])}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>{sources_section}{detail}</main><script>document.querySelectorAll('th').forEach(th=>th.onclick=()=>{{let t=th.closest('table'),i=[...th.parentNode.children].indexOf(th);[...t.tBodies[0].rows].sort((a,b)=>a.cells[i].innerText.localeCompare(b.cells[i].innerText,undefined,{{numeric:true}})).forEach(x=>t.tBodies[0].append(x))}});function search(input,table){{input?.addEventListener('input',()=>[...document.querySelectorAll('#'+table+' tbody tr')].forEach(r=>r.hidden=!r.innerText.toLowerCase().includes(input.value.toLowerCase())))}}search(document.querySelector('#linkQuery'),'links');search(document.querySelector('#query'),'users');document.querySelector('#filter')?.addEventListener('change',e=>document.querySelectorAll('#users tbody tr').forEach(r=>r.hidden=e.target.value!='all'&&r.dataset.status!=e.target.value));</script></html>"""
    with NamedTemporaryFile(
        "w", suffix=".html", prefix="stats_", encoding="utf-8", delete=False
    ) as file:
        file.write(html)
        return Path(file.name)
