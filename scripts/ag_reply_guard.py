"""Prevent manual public replies duplicating Antigravity's automatic final reply."""
import re


def manual_reply_conflicts(sender, recipients, text, active_task, kind='text', resolve_names=None,
                           original_recipients=None):
    if sender != 'ag' or kind != 'text' or not active_task:
        return False
    targets = set(recipients or [])
    if not targets:
        arrow = re.match(r'^\s*>{2,}\s*([^\n]+)', text or '')
        if arrow and resolve_names:
            targets = set(resolve_names(arrow.group(1)))
        else:
            targets = {'human'}
    # The automatic final owns the turn's original audience, whether human or
    # another member. Only calls to a new audience are separate deliveries.
    owned = set(original_recipients or [])
    origin = active_task.get('sender') if isinstance(active_task, dict) else None
    if origin and origin not in ('system', 'narrator'):
        owned.add(origin)
    if not owned:
        owned = {'human'}
    return bool(targets.intersection(owned))
