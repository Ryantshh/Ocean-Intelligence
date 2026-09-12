"""Conservative shortcuts; complex requests fall through to schema-based extraction."""
import re
from datetime import datetime, timedelta
from freight_ai.data.models import Intent


def parse_search(text, as_of):
    text = ' '.join(text.casefold().split()).rstrip('.!?')
    match = re.fullmatch(r'(?:show|list|find)(?: me)? vessel reports with at least ([\d,]+(?:\.\d+)?) (?:tonnes|tons) dwt,? including (?:historical reports|history)', text)
    if match:
        return Intent(action='query', dataset='tonnage', min_tonnes=float(match[1].replace(',', '')), include_history=True)
    match = re.fullmatch(r'(?:show|list|find)(?: me)? (?:cargo orders|orders|cargoes) (?:whose )?laycan starts between (\d{1,2}) and (\d{1,2}) ([a-z]+) (\d{4})', text)
    if match:
        try:
            start = datetime.strptime(f'{match[1]} {match[3]} {match[4]}', '%d %B %Y').date()
            end = datetime.strptime(f'{match[2]} {match[3]} {match[4]}', '%d %B %Y').date()
            return Intent(action='query', dataset='orders', start_from=start, start_to=end)
        except ValueError:
            return Intent(action='clarify', clarification='Please provide a valid, ordered laycan start-date range.')
    match = re.fullmatch(r'(?:find|show|list)(?: me)? (?:cargoes|cargo orders|orders) (?:similar|related) to (.+)', text)
    if match:
        return Intent(action='query', dataset='orders', text_filters={'cargo_description': match[1]})
    match = re.fullmatch(r'(?:show|list|find)(?: me)? (?:the )?(orders|cargo orders|cargoes|vessels|vessel reports)(?: (received|updated))? (?:from |in |for )?(?:the )?(past week|past \d+ days|last \d+ days|today|yesterday)', text)
    if match:
        span = match[3]
        end = as_of - timedelta(days=1) if span == 'yesterday' else as_of
        days = 7 if span == 'past week' else int(re.search(r'\d+',span)[0]) if re.search(r'\d+',span) else 1
        if not 1 <= days <= 3650:
            return Intent(action='clarify', clarification='Please use a report period between 1 and 3,650 days.')
        field = 'received' if match[2] == 'received' else 'updated'
        return Intent(action='query', dataset='tonnage' if match[1].startswith('vessel') else 'orders', **{field+'_from':end-timedelta(days=days-1),field+'_to':end})
    match = re.fullmatch(r'(?:show|list)(?: me)? (?:the )?(?:history|historical reports|past positions) (?:for|of) (.+)',text)
    if match:
        return Intent(action='query', dataset='tonnage', include_history=True, text_filters={'vessel_name':match[1]})
    return None
