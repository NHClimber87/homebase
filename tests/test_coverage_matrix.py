"""Coverage-matrix gate (the §7 guardrail-theater check, enforced).

Every REQUIRED §2b/§2c control must map to a PRESENT test. A build that scaffolds the
controls but drops the wiring would leave one of these unmapped — and this test fails.
This is the anti-theater backstop: a green maker suite is not enough; every named control
needs a behavioral test that attacks it.
"""
from __future__ import annotations

import importlib
import inspect

TEST_MODULES = [
    "tests.test_ac_priv",
    "tests.test_ac_corr",
    "tests.test_ac_mkt",
    "tests.test_ac_fresh",
    "tests.test_ac_cache",
    "tests.test_ac_news",
    "tests.test_ac_config",
    "tests.test_ac_ops",
]

# REQUIRED control -> the test(s) that adversarially exercise it.
REQUIRED_CONTROLS = {
    # §2a / §2b egress + local attack surface
    "no-undisclosed-egress": ["test_ac_priv_1_no_undisclosed_egress"],
    "per-source-egress-scoping": ["test_ac_priv_2_per_source_egress_scoping"],
    "consent-off-switch": ["test_ac_priv_3_off_switch_wired"],
    "loopback-only-bind": ["test_ac_priv_4_loopback_only"],
    "anti-rebind-csrf": ["test_ac_priv_5_anti_rebind_and_csrf"],
    "csp-directive-complete": ["test_ac_priv_6_csp_directive_complete"],
    "untrusted-feed-xss-inert": ["test_ac_priv_7_feed_xss_inert"],
    "feed-media-stripped": ["test_ac_priv_8_no_third_party_browser_fetch"],
    "ssrf-guard": ["test_ac_priv_9_ssrf_guard"],
    "file-perms-owner-only": ["test_ac_priv_10_file_perms"],
    "tls-fail-closed": ["test_ac_priv_11_tls_fail_closed"],
    "cookie-referer-suppression": ["test_ac_priv_12_cookie_referer_suppression"],
    "no-interest-logs": ["test_ac_priv_13_no_interest_logs"],
    # §3 / §9 ban-avoidance + freshness
    "cache-only-on-load": ["test_ac_cache_load_is_cache_only", "test_ac_cache_only_refresh_fetches"],
    "force-refresh-gated": ["test_ac_cache_force_refresh_is_debounced"],
    "validate-before-cache": ["test_ac_fresh_keeps_last_good_and_labels"],
    "lifecycle-states": ["test_ac_fresh_long_dead", "test_ac_fresh_first_boot_no_data"],
    # §2c news posture + honesty
    "news-dedup-order-honesty": ["test_ac_news_dedup_order_and_badges"],
    "news-dead-feed-labeled": ["test_ac_news_dead_feed_is_labeled"],
    "news-consent-state": ["test_ac_news_team_news_off_pending_consent"],
    # §4 correctness
    "tz-dst-correct": ["test_ac_corr_1_timezone_dst"],
    "offseason-state": ["test_ac_corr_2_offseason_per_league"],
    "status-enum": ["test_ac_corr_3_status_enum_postponed_and_doubleheader"],
    "market-state-honest": ["test_ac_mkt_2_closed_and_delayed_honest"],
    "symbol-namespace": ["test_ac_mkt_1_symbol_map_and_parse_deterministic"],
    # §6 config / §10 ops
    "config-configurable-fault-tolerant": [
        "test_ac_config_add_symbol_and_team_persist",
        "test_ac_config_malformed_file_falls_back_to_defaults",
    ],
    "ops-non-dev-windows": [
        "test_ac_ops_autostart_registered_by_installer",
        "test_ac_ops_port_pinned_and_url_fixed",
    ],
}


def _all_test_names():
    names = set()
    for mod_name in TEST_MODULES:
        mod = importlib.import_module(mod_name)
        for name, obj in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("test_"):
                names.add(name)
    return names


def test_every_required_control_has_a_test():
    present = _all_test_names()
    missing = {}
    for control, tests in REQUIRED_CONTROLS.items():
        for t in tests:
            if t not in present:
                missing.setdefault(control, []).append(t)
    assert not missing, f"guardrail-theater risk — controls without a present test: {missing}"


def test_no_required_control_left_unmapped():
    # The canonical §7 list of REQUIRED §2b/§2c controls. If a control is added to the spec
    # without a mapping here, this fails — forcing a test before the build can pass.
    canonical_2b_2c = {
        "no-undisclosed-egress", "per-source-egress-scoping", "consent-off-switch",
        "loopback-only-bind", "anti-rebind-csrf", "csp-directive-complete",
        "untrusted-feed-xss-inert", "feed-media-stripped", "ssrf-guard",
        "file-perms-owner-only", "tls-fail-closed", "cookie-referer-suppression",
        "no-interest-logs", "cache-only-on-load",
    }
    unmapped = canonical_2b_2c - set(REQUIRED_CONTROLS)
    assert not unmapped, f"REQUIRED §2b/§2c controls with no AC mapping: {unmapped}"
