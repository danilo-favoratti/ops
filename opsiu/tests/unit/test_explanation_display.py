"""Readable explanations preserve unknown references and accounting precision."""

from opsiu.interfaces.web.presentation import ExplanationDisplay


def display(category: str | None = "Locação de veículos") -> ExplanationDisplay:
    return ExplanationDisplay(
        "R01",
        "Limite mensal de locação de veículos",
        3605,
        "Maria Silva",
        120,
        category,
    )


def test_contextual_names_and_brazilian_formats() -> None:
    original = (
        "A regra R01, versão 2, controla a subcota 120 do parlamentar 3605, "
        'competência 2025/8: total "18970.47", limite "12713.00", '
        'valor envolvido "6257.47". Vigência "2023-01-01". '
        "`texto_nota` é nulo."
    )
    result = display()(original)
    assert "regra ‘Limite mensal de locação de veículos’, versão 2" in result
    assert "categoria ‘Locação de veículos’ do parlamentar Maria Silva" in result
    assert "competência 08/2025" in result
    assert "total R$ 18.970,47, limite R$ 12.713,00" in result
    assert "valor envolvido R$ 6.257,47" in result
    assert "Vigência 01/01/2023" in result
    assert "O texto da nota não está disponível" in result
    assert "parlamentar 3605" in original


def test_unknown_codes_identifiers_and_invalid_dates_are_preserved() -> None:
    original = (
        "Regra R010, subcota 1200, parlamentar 36050, documento 3605, "
        'categoria 999, regra R02, parlamentar 22, nota "123.45", '
        'competência 2025/13, "2023-02-31".'
    )
    assert display()(original) == original
    assert display(None)("subcota 120") == "subcota 120"


def test_names_are_literal_not_regex_replacements() -> None:
    formatter = ExplanationDisplay("R01", "Regra", 3605, r"Nome \1 <script>", 120, None)
    assert formatter('parlamentar "3605"') == r"parlamentar Nome \1 <script>"


def test_currency_format_is_shared_by_templates_and_expense_fields() -> None:
    from decimal import Decimal

    from jinja2 import Environment, FileSystemLoader

    from opsiu.interfaces.web.app import _DIRECTORY, _value
    from opsiu.interfaces.web.presentation import money

    env = Environment(loader=FileSystemLoader(_DIRECTORY / "templates"))
    env.filters["brl"] = money
    template = env.from_string(
        '{% from "macros.html" import money %}{{ money(value) }}'
    )
    for value, expected in [
        ("1348500.00", "R$ 1.348.500,00"),
        ("0", "R$ 0,00"),
        ("-1234.56", "R$ -1.234,56"),
        ("12.5", "R$ 12,50"),
    ]:
        assert template.render(value=Decimal(value)) == expected
        assert _value(Decimal(value)) == expected
        assert money(value) == expected


def test_prose_money_with_and_without_quotes() -> None:
    assert display()('O valor líquido fornecido é "10025.90".') == (
        "O valor líquido fornecido é R$ 10.025,90."
    )
    assert display()("valor envolvido 1348500.00") == "valor envolvido R$ 1.348.500,00"


def test_accounting_comparison_keeps_amounts_and_meaning() -> None:
    assert display()('"13500.00" menos a glosa informada de "3474.15"') == (
        "R$ 13.500,00 menos a glosa informada de R$ 3.474,15"
    )
