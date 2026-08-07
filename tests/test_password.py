import pytest

from app.security.password import (
    BCRYPT_ROUNDS,
    hash_password,
    validate_password,
    verify_password,
)


@pytest.mark.parametrize(
    "password",
    [
        "short1",
        "onlyletterslong",
        "1234567890",
    ],
)
def testWeakPasswordsAreRejected(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password(password)


def testBcryptRoundTripUsesConfiguredCost() -> None:
    hashed = hash_password("Prismatica2026!")

    assert int(hashed.split("$")[2]) == BCRYPT_ROUNDS == 12
    assert verify_password("Prismatica2026!", hashed) is True
    assert verify_password("WrongPassword2026!", hashed) is False


def testLongUtf8PasswordRoundTrip() -> None:
    password = "长密码Prismatica2026" * 10

    hashed = hash_password(password)

    assert verify_password(password, hashed) is True


def testMalformedHashReturnsFalse() -> None:
    assert verify_password("Prismatica2026!", "not-a-bcrypt-hash") is False
