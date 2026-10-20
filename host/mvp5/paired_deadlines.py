"""Same-transaction deadline comparison; no device I/O or scene-based verdicts."""
from dataclasses import replace
from app.models import delta_tick
from mvp5.examples import spi_example
from mvp5.models import Config, require

RULE_LABELS = ('GPIO · 3.5 ms', 'GPIO · 8 ms', 'SPI · 3.5 ms', 'SPI · 8 ms')


def paired_deadline_config():
    base = spi_example()
    gpio = replace(base.rules[0], min_ticks=0, max_ticks=175000, action=2)
    spi = replace(base.rules[2], min_ticks=0, max_ticks=175000, action=0)
    # Only R0 freezes the capture. 4.6 ms pre includes the full E2 identity
    # before a 3.5 ms timeout; 8.507 ms post also covers a missing 8 ms B.
    cfg = replace(base, enabled_mask=15, divisor=10, pre=23000, post=42535,
                  rules=(gpio, replace(gpio, max_ticks=400000, action=0),
                         spi, replace(spi, max_ticks=400000)))
    return Config.from_bytes(cfg.to_bytes())


def paired_capture_summary(cap, audit):
    """Bind each pair to identical actual A/B timestamps after sample auditing.

    Both TIMEOUT and both PASS are legitimate outcomes. Expected load labels
    never replace results or make a missing response into a zero delay.
    """
    m = cap.metadata
    require(m['config'] == paired_deadline_config().as_dict(), '不是同次双期限预设')
    require(audit['status'] == 'verified'
            and sorted(r['rule_id'] for r in audit['rules']) == [0, 1, 2, 3],
            '四规则原始样点必须先重判一致')
    rules = m['rules']
    starts = {r['a_tick'] for r in rules}
    require(len(starts) == 1 and None not in starts, '四规则不是同一次A事件')
    require(all(r['counts']['accepted'] == 1 for r in rules), '需要四规则各一次事务')
    pairs = []
    for target, left, right in (('GPIO', 0, 1), ('SPI', 2, 3)):
        a, b = rules[left], rules[right]
        require(a['observed_b_tick'] == b['observed_b_tick'], f'{target}两条规则观测的B不同')
        observed = a['observed_b_tick']
        pairs.append(dict(target=target, rule_ids=[left, right], deadlines_ms=[3.5, 8],
                          results=[a['result'], b['result']], observed_b_tick=observed,
                          observed_delay_ms=None if observed is None else
                          delta_tick(observed, a['a_tick']) / 50000))
    return dict(same_transaction=True, a_tick=next(iter(starts)), pairs=pairs,
                basis='verified_samples_events_and_identical_A_B_timestamps',
                independently_calibrated=False)
