from watches import CostWatch, SLAWatch


def test_sla_watch_deduplicates_and_alerts_once_per_campaign_window():
    watch = SLAWatch(minimum_delivery_ppm=900_000, minimum_volume=4)
    alerts = []
    for index, status in enumerate(["delivered", "failed", "failed", "failed"]):
        alerts.extend(watch.observe(f"event-{index}", "tenant-a", "campaign-1", status, 120 + index))
        alerts.extend(watch.observe(f"event-{index}", "tenant-a", "campaign-1", status, 120 + index))
    assert [alert.kind for alert in alerts] == ["delivery-sla"]
    assert alerts[0].value_ppm == 250_000
    assert alerts[0].window_start == 120


def test_send_rate_spike_is_separate_from_delivery_quality():
    watch = SLAWatch(minimum_delivery_ppm=0, max_send_rate_ppm=1_000_000, minimum_volume=2)
    alerts = []
    for index in range(3):
        alerts.extend(watch.observe(f"event-{index}", "tenant-a", "campaign-1", "delivered", 60))
    assert [alert.kind for alert in alerts] == ["send-rate-spike"]


def test_cost_watch_uses_exact_minor_units_and_fires_at_120_percent_once():
    watch = CostWatch()
    assert watch.observe("cost-1", "tenant-a", 600, 1_000, 600) is None
    alert = watch.observe("cost-2", "tenant-a", 600, 1_000, 601)
    assert alert is not None and alert.value_ppm == 1_200_000
    assert watch.observe("cost-3", "tenant-a", 600, 1_000, 602) is None
    assert watch.observe("cost-3", "tenant-a", 600, 1_000, 602) is None
    assert watch.observe("cost-4", "tenant-a", 1_200, 1_000, 900) is not None


def test_invalid_money_and_delivery_status_fail_closed():
    watch = CostWatch()
    try:
        watch.observe("bad", "tenant-a", -1, 1_000, 600)
        assert False, "negative provider cost was accepted"
    except ValueError:
        pass
    sla = SLAWatch()
    try:
        sla.observe("bad", "tenant-a", "campaign", "unknown", 60)
        assert False, "unknown delivery state was accepted"
    except ValueError:
        pass
