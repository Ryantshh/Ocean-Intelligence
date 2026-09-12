"""Readable tables built only from deterministic tool output."""
from freight_ai.presentation import display, intent_description


def record_response(result, intent):
    rows = result['records']
    semantic = 'semantic_scores' in result
    title = (f"**{len(rows)} semantic suggestions** (not exact matches)." if semantic else
             f"I found **{result['total_count']:,} matching {'cargo orders' if intent.dataset == 'orders' else 'vessel reports'}** as of **{display(result.get('as_of'))}**.")
    lines = [title, '**Search applied:** ' + intent_description(intent.model_dump(mode='json'))]
    if not rows:
        return '\n\n'.join(lines + ['No matching records in the selected data source. This does not establish that none exist in the wider market.'])
    fields = ([('record_id','Order ID'),('cargo_type','Cargo'),('load_port','Load port'),
               ('discharge_port','Discharge port'),('cargo_weight_min','Min tonnes'),
               ('cargo_weight_max','Max tonnes'),('laycan_start','Laycan start'),('laycan_end','Laycan end')]
              if intent.dataset == 'orders' else
              [('record_id','Report ID'),('vessel_name','Vessel'),('dwt','DWT (tonnes)'),
               ('open_area','Open location'),('open_date_start','Open from'),('open_date_end','Open until'),
               ('commercial_status','Commercial status')])
    def cell(value):
        return display(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ').replace('<','&lt;').replace('>','&gt;')
    headers = [label for _,label in fields] + ['Source']
    if semantic:
        headers.append('Similarity')
    table = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for row in rows:
        source = row.get('source', {})
        values = [cell(row.get(key)) for key,_ in fields]
        values.append(cell(f"{source.get('file','Unknown')}, {source.get('sheet','')}, row {source.get('row','?')}"))
        if semantic:
            values.append(f"{result['semantic_scores'][row['record_id']]:.3f}")
        table.append('| ' + ' | '.join(values) + ' |')
    lines.append('\n'.join(table))
    lines.append('Quantities are in metric tonnes. Source references identify reported records, not confirmed fixtures or completed movements.')
    if result.get('truncated'):
        lines.append(f"Showing {len(rows)} of {result['total_count']:,} results. Narrow the search for more specific records.")
    if semantic:
        lines.append('Ranked locally after hard filters. Similarity does not confirm cargo compatibility, geographical feasibility or a vessel assignment.')
    if intent.dataset == 'tonnage':
        lines.append('DWT includes fuel and stores; it is not usable cargo capacity. Reported positions do not confirm commercial availability or arrival feasibility.')
    if result.get('aggregation'):
        lines.append('**Calculated totals:** ' + '; '.join(f"{key}: {display(value)}" if not isinstance(value,dict) else f"{key.replace('_',' ')}: {display(value.get('known_sum'))}; missing: {value.get('missing_count',0)}" for key,value in result['aggregation'].items()))
    return '\n\n'.join(lines)
