"""ロケット演出 (manager/rocketfx.py) の形と経路のテスト。

見た目そのものは目で見るしかないが、「機体の大きさと位置が全状態で
そろっていること」「発射が到達点を追い越して戻ってこないこと」は
数値で固定できる。ここが崩れると演出の基点 (main.py の _rocket_base)
とずれるため、テストで押さえておく。

flet は CI の依存に含めないため、未導入環境では自動スキップされる。
"""
import pytest

pytest.importorskip('flet')

from manager import rocketfx                                # noqa: E402

# 残骸 (機体ではない) 以外の全状態
ROCKET_STATES = ('idle', 'ignited', 'smoking', 'ignited_smoking')
ALL_STATES = ROCKET_STATES + ('wreck',)


def _rocket_of(slot):
    """スロットの中の機体 (煙ではないほう) を取り出す."""
    children = slot.content.controls
    return children[-1]


def test_zone_slots_are_all_the_same_size():
    """5 状態とも同じ寸法のスロット (行の高さが状態で動かないこと)."""
    sizes = {s: (rocketfx.zone(s)[0].width, rocketfx.zone(s)[0].height)
             for s in ALL_STATES}
    assert set(sizes.values()) == {(rocketfx.ZONE_W, rocketfx.ZONE_H)}


def test_rocket_size_and_position_never_change():
    """炎が出ても煙が出ても、機体の大きさと位置は承認なしのときと同じ.

    2026-08 まではスロットの高さが足りず点火状態だけ縮み、煙つきは
    機体が右へずれていた。
    """
    base = _rocket_of(rocketfx.zone('idle')[0])
    for state in ROCKET_STATES:
        rocket = _rocket_of(rocketfx.zone(state)[0])
        assert (rocket.width, rocket.height) == (base.width, base.height), \
            '%s で機体の大きさが変わっている' % state
        assert (rocket.left, rocket.top) == (base.left, base.top), \
            '%s で機体の位置が変わっている' % state


def test_smoke_does_not_move_the_rocket():
    """煙はスロットの左側 (機体の左) に置き、機体には重ならないこと."""
    slot = rocketfx.zone('smoking')[0]
    puffs = slot.content.controls[:-1]
    assert puffs, '煙が出ていない'
    for p in puffs:
        assert p.left + p.width <= rocketfx.ZONE_LEFT + 1


def test_zone_states_have_a_tooltip():
    """装飾ではなく「読める部品」であること (説明が出る)."""
    for state in ALL_STATES:
        assert rocketfx.zone(state)[0].tooltip


def _launch_ys(start_y, target_y=92, steps=40):
    """発射の経路の y を時系列で返す (離陸 → 弧 → 到達点)."""
    climb, top_y, ctrl_y = rocketfx.launch_path(start_y, target_y)
    ys = [start_y - climb * (i / steps) ** 2 for i in range(steps + 1)]
    for i in range(steps + 1):
        q = i / steps
        e = 1 - (1 - q) * (1 - q)
        u = 1 - e
        ys.append(u * u * top_y + 2 * u * e * ctrl_y + e * e * target_y)
    return ys


@pytest.mark.parametrize('start_y', [120, 201, 274, 388, 600])
def test_launch_never_overshoots_the_target(start_y):
    """上がりきったところで ✨。到達点より上へ行き過ぎて戻らないこと.

    経路の制御点が到達点の 60px 上に固定だったため、基点が画面の
    上寄りにあるカードでは行き過ぎてから下がってきていた (2026-08)。
    """
    target_y = 92
    ys = _launch_ys(start_y, target_y)
    assert min(ys) >= target_y - 0.5, '到達点より上へ行き過ぎている'
    # 単調に上がる (画面座標なので y は減っていく) こと
    for a, b in zip(ys, ys[1:]):
        assert b <= a + 0.5, '途中で下がってきている'
    assert abs(ys[-1] - target_y) < 0.5, '到達点で終わっていない'


def test_launch_path_is_unchanged_for_far_cards():
    """遠いカード (これまで問題のなかった位置) の動きは変えないこと."""
    climb, top_y, ctrl_y = rocketfx.launch_path(388, 92)
    assert climb == 105                 # 従来と同じ高さまで離陸する
    assert (top_y, ctrl_y) == (283, 223)


def test_launch_path_is_clamped_for_near_cards():
    """到達点に近いカードでは、上がる高さも制御点も頭打ちになること."""
    climb, top_y, ctrl_y = rocketfx.launch_path(120, 92)
    assert climb < 105
    assert top_y >= 92 and ctrl_y >= 92
