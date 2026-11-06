"""
«Окна и анимации» в админке (найдено 06.11 при прогоне всех 40 сцен в браузере):
  • окна прошлых сцен не закрывались — к 15-й на экране висели 6 окон, «Напомнить друзьям» показывало «Давай познакомимся»;
  • сундук после «повторить» не открывался (флаг «занято»), серия гасла через 350 мс (отложенное скрытие закрывашки);
  • сцена в чате уводила на другую страницу — листание обрывалось, сцены 20–40 стрелками были недоступны;
  • поверх сцен всплывали анкета, тур, «Адам написал»; тосты «Голоса Адама» и демо-карточки Главной переезжали в следующие сцены;
  • панель закрывала кнопки внизу окон («Не сейчас» у подписки) и сами тосты.
Проверено глазами и скриптом на всех сценах; здесь — страховка, что механизм не потеряется.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
COACH = (ROOT / "webapp" / "static" / "ai_coach.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")


def test_each_scene_starts_on_a_clean_stage():
    play = APP[APP.index("async function playSimScene() {"):][:400]
    assert "++simRunToken" in play and "await resetSimStage();" in play and "token !== simRunToken" in play
    reset = APP[APP.index("async function resetSimStage() {"):][:1200]
    assert 'document.getElementById("chestClose")?.click()' in reset, "сундук закрывается как «Не сейчас» — флаг «занято» снимается"
    assert "if (simIsOpen(id)) quiet(close)" in reset, "закрывашки только для открытых окон — их отложенное скрытие не гасит новую сцену"
    assert "SIM_SETTLE_MS" in reset and "forceHideSimOverlays();" in reset and "resetSimHome();" in reset


def test_auto_popups_stay_away_in_scene_mode():
    assert "const SIM_REQUESTED" in APP
    boot = APP[APP.index("if (!SIM_REQUESTED) {"):][:300]
    assert "maybeShowStartQuiz();" in boot and "maybeShowAdamIntro();" in boot
    assert 'typeof SIM_REQUESTED !== "undefined" && SIM_REQUESTED' in APP[APP.index("function scheduleAdamNudge"):][:300]


def test_the_chat_scene_keeps_the_scene_navigation():
    scene = APP[APP.index("  trial_chat_offer: () => {"):][:700]
    assert "&prev=" in scene and "&next=" in scene
    assert "simChatBar" in COACH and "'Следующее окно'" in COACH and "'/?sim=' + encodeURIComponent(key)" in COACH


def test_the_bar_can_move_and_toasts_stay_visible():
    assert 'data-sim="move"' in APP and 'act === "move"' in APP and "adam_sim_bar_top" in APP
    assert ".lab-bar.lab-bar--top{" in CSS
    assert "body.has-sim-bar:not(.has-sim-bar-top) .toast{" in CSS
