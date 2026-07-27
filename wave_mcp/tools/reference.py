"""Reference-data tools: user, currencies, countries, account taxonomy, OAuth app.

These back the enum values other tools require -- currency codes, country and
province codes, and the account type/subtype taxonomy needed by
`wave_create_account`.
"""

from __future__ import annotations

from typing import Optional

from ..formatting import kv_block, render, table, yes_no
from ..runtime import get_client, mcp

GET_USER = """
query GetUser {
  user { id firstName lastName defaultEmail createdAt modifiedAt }
}
"""

LIST_CURRENCIES = """
query ListCurrencies { currencies { code symbol name plural exponent } }
"""

GET_CURRENCY = """
query GetCurrency($code: CurrencyCode!) {
  currency(code: $code) { code symbol name plural exponent }
}
"""

LIST_COUNTRIES = """
query ListCountries {
  countries { code name nameWithArticle currency { code symbol } }
}
"""

GET_COUNTRY = """
query GetCountry($code: CountryCode!) {
  country(code: $code) {
    code
    name
    nameWithArticle
    currency { code symbol name }
    provinces { code name slug }
  }
}
"""

GET_PROVINCE = """
query GetProvince($code: String!) {
  province(code: $code) { code name slug }
}
"""

LIST_ACCOUNT_TYPES = """
query ListAccountTypes {
  accountTypes { name value normalBalanceType }
}
"""

LIST_ACCOUNT_SUBTYPES = """
query ListAccountSubtypes {
  accountSubtypes {
    name
    value
    description
    archivable
    systemCreated
    type { name value normalBalanceType }
  }
}
"""

GET_OAUTH_APPLICATION = """
query GetOAuthApplication {
  oAuthApplication {
    id
    name
    description
    clientId
    logoUrl
    createdAt
    modifiedAt
  }
}
"""


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_user(response_format: str = "markdown") -> str:
    """Get the Wave user account that owns the current access token.

    Useful for confirming which account a token authenticates as.

    Args:
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(GET_USER)
    user = data.get("user")
    if not user:
        return "Wave returned no user for this token. The token may be invalid or revoked."

    def as_markdown() -> str:
        name = " ".join(p for p in [user.get("firstName"), user.get("lastName")] if p)
        return f"**{name or 'Wave user'}**\n\n" + kv_block(
            [
                ("ID", f"`{user['id']}`"),
                ("Email", user.get("defaultEmail")),
                ("Created", user.get("createdAt")),
                ("Modified", user.get("modifiedAt")),
            ]
        )

    return render(user, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_currencies(
    search: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """List the currency codes Wave supports.

    Wave supports about 160 currencies, so pass `search` to narrow the list.

    Args:
        search: Case-insensitive filter on code or name, e.g. "CAD" or "dollar".
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(LIST_CURRENCIES)
    currencies = data.get("currencies") or []
    if search:
        needle = search.lower()
        currencies = [
            c
            for c in currencies
            if needle in c["code"].lower() or needle in c["name"].lower()
        ]

    def as_markdown() -> str:
        if not currencies:
            return f"No currencies matched `{search}`."
        heading = f"**Currencies** ({len(currencies)} shown)"
        return f"{heading}\n\n" + table(
            currencies,
            [
                ("Code", "code"),
                ("Name", "name"),
                ("Symbol", "symbol"),
                ("Decimal places", "exponent"),
            ],
        )

    return render(currencies, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_currency(code: str, response_format: str = "markdown") -> str:
    """Get one currency by ISO 4217 code.

    Args:
        code: Currency code such as "USD", "CAD", or "EUR".
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(GET_CURRENCY, {"code": code.upper()})
    currency = data.get("currency")
    if not currency:
        return f"No currency found for code `{code}`. Call wave_list_currencies to see valid codes."

    def as_markdown() -> str:
        return f"**{currency['name']} ({currency['code']})**\n\n" + kv_block(
            [
                ("Symbol", currency.get("symbol")),
                ("Plural", currency.get("plural")),
                ("Decimal places", currency.get("exponent")),
            ]
        )

    return render(currency, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_countries(
    search: Optional[str] = None,
    response_format: str = "markdown",
) -> str:
    """List the countries Wave supports, with each one's default currency.

    Args:
        search: Case-insensitive filter on country code or name.
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(LIST_COUNTRIES)
    countries = data.get("countries") or []
    if search:
        needle = search.lower()
        countries = [
            c
            for c in countries
            if needle in c["code"].lower() or needle in c["name"].lower()
        ]

    def as_markdown() -> str:
        if not countries:
            return f"No countries matched `{search}`."
        return f"**Countries** ({len(countries)} shown)\n\n" + table(
            countries,
            [
                ("Code", "code"),
                ("Name", "name"),
                ("Currency", lambda r: (r.get("currency") or {}).get("code", "-")),
            ],
        )

    return render(countries, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_country(code: str, response_format: str = "markdown") -> str:
    """Get one country and its provinces or states.

    Use this to find the province codes that address fields expect.

    Args:
        code: ISO 3166-1 alpha-2 code such as "US", "CA", or "GB".
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(GET_COUNTRY, {"code": code.upper()})
    country = data.get("country")
    if not country:
        return f"No country found for code `{code}`. Call wave_list_countries to see valid codes."

    def as_markdown() -> str:
        currency = country.get("currency") or {}
        head = f"**{country['name']} ({country['code']})**\n\n" + kv_block(
            [("Default currency", f"{currency.get('code')} ({currency.get('name')})")]
        )
        provinces = country.get("provinces") or []
        if not provinces:
            return head
        return (
            f"{head}\n\n**Provinces / states** ({len(provinces)})\n\n"
            + table(provinces, [("Code", "code"), ("Name", "name")])
        )

    return render(country, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_province(code: str, response_format: str = "markdown") -> str:
    """Get one province or state by its code.

    Args:
        code: Province code, typically country-qualified, e.g. "CA-ON" or "US-NY".
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(GET_PROVINCE, {"code": code})
    province = data.get("province")
    if not province:
        return (
            f"No province found for code `{code}`. Codes are usually "
            'country-qualified ("CA-ON", "US-NY"); call wave_get_country to list them.'
        )

    def as_markdown() -> str:
        return f"**{province['name']}**\n\n" + kv_block(
            [("Code", f"`{province['code']}`"), ("Slug", province.get("slug"))]
        )

    return render(province, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_account_types(response_format: str = "markdown") -> str:
    """List the five top-level account types in Wave's chart of accounts.

    ASSET, LIABILITY, EQUITY, INCOME, and EXPENSE, each with its normal balance.

    Args:
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(LIST_ACCOUNT_TYPES)
    types = data.get("accountTypes") or []

    def as_markdown() -> str:
        return "**Account types**\n\n" + table(
            types,
            [
                ("Name", "name"),
                ("Value", "value"),
                ("Normal balance", "normalBalanceType"),
            ],
        )

    return render(types, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_list_account_subtypes(
    account_type: Optional[str] = None,
    creatable_only: bool = False,
    response_format: str = "markdown",
) -> str:
    """List account subtypes -- the value `wave_create_account` needs.

    Every account belongs to a subtype (CASH_AND_BANK, EXPENSE, INCOME, ...),
    which in turn determines its type. Some subtypes are system-created and
    cannot be used for new accounts; pass `creatable_only` to hide those.

    Args:
        account_type: Filter to one type: ASSET, LIABILITY, EQUITY, INCOME, EXPENSE.
        creatable_only: Exclude system-created subtypes unavailable to new accounts.
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(LIST_ACCOUNT_SUBTYPES)
    subtypes = data.get("accountSubtypes") or []

    if account_type:
        wanted = account_type.upper()
        subtypes = [
            s for s in subtypes if (s.get("type") or {}).get("value") == wanted
        ]
    if creatable_only:
        subtypes = [s for s in subtypes if not s.get("systemCreated")]

    def as_markdown() -> str:
        if not subtypes:
            return (
                "No account subtypes matched. Valid account_type values are "
                "ASSET, LIABILITY, EQUITY, INCOME, EXPENSE."
            )
        return f"**Account subtypes** ({len(subtypes)} shown)\n\n" + table(
            subtypes,
            [
                ("Subtype", "value"),
                ("Name", "name"),
                ("Type", lambda r: (r.get("type") or {}).get("value", "-")),
                ("Archivable", lambda r: yes_no(r.get("archivable"))),
                ("System", lambda r: yes_no(r.get("systemCreated"))),
            ],
        )

    return render(subtypes, response_format, as_markdown)


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def wave_get_oauth_application(response_format: str = "markdown") -> str:
    """Get the OAuth application that issued the current access token.

    Args:
        response_format: "markdown" or "json".
    """
    data = await get_client().execute(GET_OAUTH_APPLICATION)
    app = data.get("oAuthApplication")
    if not app:
        return (
            "Wave returned no OAuth application for this token. Personal "
            "access tokens are not tied to an application, so this is expected "
            "unless the token came from an OAuth flow."
        )

    def as_markdown() -> str:
        return f"**{app['name']}**\n\n" + kv_block(
            [
                ("ID", f"`{app['id']}`"),
                ("Client ID", f"`{app.get('clientId')}`"),
                ("Description", app.get("description")),
                ("Logo URL", app.get("logoUrl")),
                ("Created", app.get("createdAt")),
                ("Modified", app.get("modifiedAt")),
            ]
        )

    return render(app, response_format, as_markdown)
