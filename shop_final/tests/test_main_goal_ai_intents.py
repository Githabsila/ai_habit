
import pytest
import habit_intents


def test_main_goal_regex_set():
    m = habit_intents._MAIN_GOAL_SET_RE.match("поставь главную задачу дня закончить отчёт")
    assert m
    assert m.group(1) == "закончить отчёт"


def test_main_goal_regex_alias():
    m = habit_intents._MAIN_GOAL_SET_RE.match("добавь главную привычку дня прочитать 20 страниц")
    assert m
    assert m.group(1) == "прочитать 20 страниц"


def test_main_goal_complete():
    assert habit_intents._MAIN_GOAL_COMPLETE_RE.match("отметь главную задачу дня")


def test_main_goal_delete():
    assert habit_intents._MAIN_GOAL_DELETE_RE.match("удали главную цель дня")


def test_main_goal_edit():
    m = habit_intents._MAIN_GOAL_EDIT_RE.match("измени главную задачу дня на подготовить презентацию")
    assert m
    assert m.group(1) == "подготовить презентацию"
