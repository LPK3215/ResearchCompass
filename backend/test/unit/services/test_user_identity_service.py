"""用户身份服务单元测试。

覆盖注册功能的核心校验逻辑：用户名校验、UID 生成、手机号校验。
这些纯函数不依赖数据库，适合作为单元测试快速验证边界条件。
"""

import pytest

from yuxi.services.user_identity_service import (
    generate_uid,
    generate_unique_uid,
    is_valid_phone_number,
    validate_username,
)


@pytest.mark.parametrize(
    ("username", "expected_valid"),
    [
        ("张三", True),
        ("alice", True),
        ("user_001", True),
        ("科研助手2026", True),
        ("Ab", True),
    ],
)
def test_validate_username_accepts_valid_names(username, expected_valid):
    is_valid, error_msg = validate_username(username)
    assert is_valid is expected_valid
    assert error_msg == ""


@pytest.mark.parametrize(
    ("username", "expected_error"),
    [
        ("", "用户名不能为空"),
        ("A", "用户名长度不能少于2个字符"),
        ("a" * 21, "用户名长度不能超过20个字符"),
        ("user@name", "用户名只能包含中文、英文、数字和下划线"),
        ("user-name", "用户名只能包含中文、英文、数字和下划线"),
        ("用户名!符号", "用户名只能包含中文、英文、数字和下划线"),
    ],
)
def test_validate_username_rejects_invalid_names(username, expected_error):
    is_valid, error_msg = validate_username(username)
    assert is_valid is False
    assert error_msg == expected_error


def test_generate_uid_produces_lowercase_alphanumeric():
    uid = generate_uid("alice")
    assert uid == "alice"
    assert uid.islower()


def test_generate_uid_prepends_u_when_starting_with_digit():
    uid = generate_uid("123abc")
    assert uid.startswith("u")
    assert uid[1:].isalnum()


def test_generate_uid_fallback_for_unmappable_name():
    uid = generate_uid("用户名")
    assert len(uid) >= 2
    assert uid[:20] == uid


def test_generate_unique_uid_returns_base_when_no_conflict():
    base_uid = generate_uid("alice")
    result = generate_unique_uid("alice", [])
    assert result == base_uid


def test_generate_unique_uid_appends_counter_on_conflict():
    base_uid = generate_uid("alice")
    result = generate_unique_uid("alice", [base_uid])
    assert result == f"{base_uid}1"


def test_generate_unique_uid_increments_counter_until_available():
    base_uid = generate_uid("alice")
    existing = [f"{base_uid}{i}" for i in range(1, 100)]
    result = generate_unique_uid("alice", [base_uid, *existing])
    assert result == f"{base_uid}100"


@pytest.mark.parametrize(
    "phone",
    [
        "13800138000",
        "15912345678",
        "18600000000",
        "17098765432",
    ],
)
def test_is_valid_phone_number_accepts_valid_numbers(phone):
    assert is_valid_phone_number(phone) is True


@pytest.mark.parametrize(
    "phone",
    [
        "12345678901",
        "1380013800",
        "2380013800",
        "138001380001",
        "abc",
        "12 34 56 78 90 1",
    ],
)
def test_is_valid_phone_number_rejects_invalid_numbers(phone):
    assert is_valid_phone_number(phone) is False


def test_is_valid_phone_number_rejects_empty():
    assert is_valid_phone_number("") is False


def test_is_valid_phone_number_normalizes_separators():
    assert is_valid_phone_number("138-0013-8000") is True
    assert is_valid_phone_number("138 0013 8000") is True
    assert is_valid_phone_number("(138)00138000") is True
