import pytest

from distro_recovery.backends.dnf5 import Dnf5Backend
from distro_recovery.classify import (
    Context,
    DecisionConflict,
    classify,
    classify_all,
    is_system_rule,
)
from distro_recovery.model import (
    Baseline,
    Confidence,
    Group,
    InstalledPkg,
    Reason,
    ReasonCode,
    UserHistory,
)

BASELINE = Baseline(
    environment="env",
    groups=frozenset({"core"}),
    packages=frozenset({"bash", "nano"}),
    protected=frozenset({"gnome-shell"}),
    system_release=frozenset({"fedora-release-workstation"}),
)
GROUPS = [Group("development-tools", "Development Tools", frozenset({"gcc", "git"}))]


def ctx(**kw):
    base = {
        "include": frozenset(),
        "exclude": frozenset(),
        "baseline": BASELINE,
        "groups": GROUPS,
        "history": UserHistory(image_tx_ids=frozenset({1}), installs={}),
        "leaves": frozenset(),
        "brew": frozenset(),
    }
    return Context(**{**base, **kw})


def pkg(name, reason=Reason.USER, repo="updates"):
    return InstalledPkg(name, "x86_64", reason, repo, "Fedora Project")


def check(p, c, confidence, code, group=None):
    got = classify(p, c)
    assert (got.confidence, got.reason_code, got.group) == (confidence, code, group)


H, R, X = Confidence.HIGH, Confidence.REVIEW, Confidence.EXCLUDED
C = ReasonCode


def test_user_excluded():
    check(pkg("vlc"), ctx(exclude=frozenset({"vlc"})), X, C.USER_EXCLUDED)


def test_user_included():
    check(pkg("htop", Reason.DEPENDENCY), ctx(include=frozenset({"htop"})), H, C.USER_INCLUDED)


def test_system_rule():
    check(pkg("kernel-core"), ctx(), X, C.SYSTEM_RULE)


@pytest.mark.parametrize("reason", [Reason.DEPENDENCY, Reason.WEAK_DEPENDENCY, Reason.CLEAN])
def test_dependency(reason):
    check(pkg("libfoo", reason), ctx(), X, C.DEPENDENCY)


def test_via_group():
    check(pkg("gcc", Reason.GROUP), ctx(), X, C.VIA_GROUP, group="development-tools")


def test_baseline_group():
    check(pkg("bash", Reason.GROUP), ctx(), X, C.BASELINE_GROUP)


def test_group_package_in_no_known_group_needs_review():
    check(pkg("mystery", Reason.GROUP), ctx(), R, C.USER_UNCONFIRMED)


def test_user_history():
    h = UserHistory(frozenset({1}), {"vlc": frozenset({44})})
    check(pkg("vlc"), ctx(history=h), H, C.USER_HISTORY)


def test_user_leaf():
    check(pkg("tmux"), ctx(leaves=frozenset({"tmux"})), H, C.USER_LEAF)


def test_user_unconfirmed():
    check(pkg("tmux"), ctx(), R, C.USER_UNCONFIRMED)


def test_in_baseline_but_user():
    h = UserHistory(frozenset({1}), {"nano": frozenset({1, 30})})
    check(pkg("nano"), ctx(history=h), R, C.IN_BASELINE_BUT_USER)


def test_baseline_installed_only_by_image_transactions():
    h = UserHistory(frozenset({1, 2}), {"nano": frozenset({1, 2})})
    check(pkg("nano"), ctx(history=h), X, C.BASELINE)


def test_second_image_transaction_is_not_user_activity():
    h = UserHistory(frozenset({1, 2}), {"nano": frozenset({2})})
    check(pkg("nano"), ctx(history=h), X, C.BASELINE)


def test_installed_only_by_the_image_is_baseline_even_if_leaf():
    h = UserHistory(frozenset({1, 2}), {"dracut-live": frozenset({2})}, frozenset({"dracut-live"}))
    check(pkg("dracut-live"), ctx(history=h, leaves=frozenset({"dracut-live"})), X, C.BASELINE)


def test_image_package_without_user_install_is_baseline():
    h = UserHistory(frozenset({1}), {}, frozenset({"lvm2"}))
    check(pkg("lvm2"), ctx(history=h), X, C.BASELINE)


def test_image_package_the_user_installed_later_is_user_history():
    h = UserHistory(frozenset({1}), {"htop": frozenset({1, 30})}, frozenset({"htop"}))
    check(pkg("htop"), ctx(history=h), H, C.USER_HISTORY)


def test_unknown_to_history_and_not_a_leaf_still_needs_review():
    check(pkg("tmux"), ctx(), R, C.USER_UNCONFIRMED)


def test_local_rpm_overrides_high():
    h = UserHistory(frozenset({1}), {"chatgpt": frozenset({20})})
    check(pkg("chatgpt", repo="@commandline"), ctx(history=h), R, C.LOCAL_RPM)


def test_duplicated_in_brew_overrides_high():
    h = UserHistory(frozenset({1}), {"htop": frozenset({20})})
    check(pkg("htop"), ctx(history=h, brew=frozenset({"htop"})), R, C.DUPLICATED_IN_BREW)


def test_local_rpm_wins_over_brew_duplicate():
    c = ctx(leaves=frozenset({"x"}), brew=frozenset({"x"}))
    check(pkg("x", repo="@commandline"), c, R, C.LOCAL_RPM)


def test_overrides_do_not_touch_excluded_baseline_or_dependency():
    check(pkg("nano"), ctx(brew=frozenset({"nano"})), X, C.BASELINE)
    check(pkg("libfoo", Reason.DEPENDENCY, "@commandline"), ctx(), X, C.DEPENDENCY)


def test_user_included_is_not_downgraded_by_local_or_brew():
    c = ctx(include=frozenset({"x"}), brew=frozenset({"x"}))
    check(pkg("x", repo="@commandline"), c, H, C.USER_INCLUDED)


def test_precedence_exclude_over_include_is_a_validation_error():
    with pytest.raises(DecisionConflict, match="vlc"):
        ctx(include=frozenset({"vlc"}), exclude=frozenset({"vlc"}))


def test_include_beats_system_rule():
    check(pkg("kernel-tools"), ctx(include=frozenset({"kernel-tools"})), H, C.USER_INCLUDED)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kernel", True),
        ("kernel-modules-extra", True),
        ("kmod-nvidia-7.2.11-200.fc44.x86_64", True),
        ("kmod", False),
        ("kmod-libs", False),
        ("akmod-nvidia", False),
        ("linux-firmware", True),
        ("glibc-langpack-pt", True),
        ("anaconda-core", True),
        ("fedora-release-workstation", True),
        ("gnome-shell", True),
        ("vlc", False),
    ],
)
def test_is_system_rule(name, expected):
    assert is_system_rule(name, BASELINE) is expected


def test_classify_all_dedupes_names_and_is_sorted():
    pkgs = [pkg("zsh"), pkg("kernel"), pkg("kernel"), pkg("alpha")]
    out = classify_all(pkgs, ctx())
    assert [c.name for c in out] == ["alpha", "kernel", "zsh"]


def test_classify_all_on_real_fixtures(runner):
    b = Dnf5Backend(runner)
    c = Context(
        include=frozenset(),
        exclude=frozenset(),
        baseline=b.baseline(),
        groups=b.installed_groups(),
        history=b.history_user_installs(),
        leaves=b.leaves(),
        brew=frozenset(),
    )
    got = {x.name: x for x in classify_all(b.installed(), c)}
    assert got["pavucontrol"].reason_code is C.USER_HISTORY
    assert got["chatgpt"].reason_code is C.LOCAL_RPM
    assert got["kernel-core"].reason_code is C.SYSTEM_RULE
    assert got["7zip"].reason_code is C.DEPENDENCY
    assert {x.reason_code for x in got.values()} >= {
        C.USER_HISTORY,
        C.DEPENDENCY,
        C.SYSTEM_RULE,
        C.BASELINE_GROUP,
        C.LOCAL_RPM,
    }
