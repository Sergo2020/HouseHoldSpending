from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path
import csv

import pandas as pd


CANONICAL_COLUMNS = [
    "id", "user", "source", "table_name", "date", "description", "amount", "direction", "raw_type"
]


def _clean(value: object) -> str:
    return "" if pd.isna(value) else " ".join(str(value).replace("\n", " ").split())


def _number(value: object) -> float:
    if pd.isna(value) or value == "":
        return 0.0
    if isinstance(value, str):
        value = value.replace(",", "").replace("₪", "").strip()
    return float(value)


def _date(value: object) -> pd.Timestamp:
    if isinstance(value, (int, float)) and not pd.isna(value):
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
    return pd.to_datetime(value, dayfirst=True, errors="coerce")


def _row_id(user: str, source: str, row_number: int, date: pd.Timestamp, description: str, amount: float) -> str:
    payload = f"{user}|{source}|{row_number}|{date.date() if pd.notna(date) else ''}|{description}|{amount:.2f}"
    return sha256(payload.encode("utf-8")).hexdigest()[:20]


def _frame(records: list[dict]) -> pd.DataFrame:
    result = pd.DataFrame(records, columns=CANONICAL_COLUMNS)
    if result.empty:
        return result
    result["date"] = pd.to_datetime(result["date"]).dt.normalize()
    result["amount"] = result["amount"].astype(float)
    return result.dropna(subset=["date"]).sort_values("date", kind="stable").reset_index(drop=True)


def deduplicate_transactions(transactions: pd.DataFrame) -> pd.DataFrame:
    """Reconcile overlapping exports without collapsing repeats inside one export."""
    if transactions.empty:
        return transactions
    identity = ["user", "source", "date", "description", "amount", "direction", "raw_type"]
    if "_import_file" not in transactions:
        return transactions.drop_duplicates(subset=identity, keep="first")

    kept_groups = []
    for _, group in transactions.groupby(identity, dropna=False, sort=False):
        # A transaction can genuinely be repeated in one export. Across
        # overlapping exports retain the file with the greatest occurrence
        # count (the earliest file wins ties), rather than summing copies.
        counts = group.groupby("_import_file", sort=False).size()
        kept_file = counts.index[counts.argmax()]
        kept_groups.append(group[group["_import_file"].eq(kept_file)])
    return pd.concat(kept_groups, ignore_index=True)


def _find_header(raw: pd.DataFrame, required: tuple[str, ...]) -> int:
    for index, row in raw.iterrows():
        text = " ".join(_clean(value) for value in row.tolist())
        if all(word in text for word in required):
            return int(index)
    raise ValueError(f"Could not find spreadsheet header containing: {required}")


def _header_columns(raw: pd.DataFrame, header: int) -> list[str]:
    return [_clean(value) for value in raw.iloc[header].tolist()]


def _column_index(columns: list[str], label: str) -> int:
    return next(index for index, value in enumerate(columns) if label in value)


class TransactionParser(ABC):
    """Base parser for a recognized bank or card export table."""

    source: str
    identity_key: str

    def __init__(self, path: Path, user: str) -> None:
        self.path = path
        self.user = user

    @classmethod
    @abstractmethod
    def matches(cls, path: Path) -> bool:
        """Return whether the export's table structure belongs to this parser."""

    @abstractmethod
    def read_records(self) -> list[dict]:
        """Read this export into canonical transaction records."""

    def parse(self) -> pd.DataFrame:
        return _frame(self.read_records())

    def record(
        self, row_number: int, date: pd.Timestamp, description: str, amount: float, raw_type: str,
        identity_key: str | None = None,
    ) -> dict:
        return {
            "id": _row_id(self.user, identity_key or self.identity_key, row_number, date, description, amount),
            "user": self.user,
            "source": self.source,
            "date": date,
            "description": description,
            "amount": amount,
            "direction": "credit" if amount >= 0 else "debit",
            "raw_type": raw_type,
        }


class BankParser(TransactionParser, ABC):
    source = "bank"


class CardParser(TransactionParser, ABC):
    source = "card"


class DiscountBankParser(BankParser):
    """Discount Bank CSV export, identified by its transaction columns."""

    identity_key = "account_ksu"
    signature = ("יום ערך", "תיאור התנועה", "זכות/חובה")

    @classmethod
    def matches(cls, path: Path) -> bool:
        if path.suffix.lower() != ".csv":
            return False
        with path.open(encoding="utf-8-sig", newline="") as handle:
            header = " ".join(csv.DictReader(handle).fieldnames or [])
        return all(label in header for label in cls.signature)

    def read_records(self) -> list[dict]:
        records = []
        # This export contains a trailing empty field and occasional extra fields;
        # DictReader retains the named columns instead of rejecting the whole file.
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                try:
                    amount = _number(row.get("זכות/חובה ₪"))
                except ValueError:
                    # A few fee descriptions contain an unquoted comma. Their values
                    # shift right one column, while the balance field has the amount.
                    amount = _number(row.get(" יתרה ₪"))
                date = _date(row.get("יום ערך") or row.get("תאריך"))
                description = _clean(row.get("תיאור התנועה"))
                records.append(self.record(index, date, description, amount, _clean(row.get("ערוץ ביצוע"))))
        return records


class BenLeumiBankParser(BankParser):
    """Ben Leumi account workbook, identified by credit/debit account columns."""

    identity_key = "account_ser"
    signature = ("תאריך ערך", "זכות", "חובה", "תיאור", "סוג פעולה")

    @classmethod
    def matches(cls, path: Path) -> bool:
        if path.suffix.lower() != ".xlsx":
            return False
        raw = pd.read_excel(path, header=None)
        try:
            _find_header(raw, cls.signature)
        except ValueError:
            return False
        return True

    def read_records(self) -> list[dict]:
        raw = pd.read_excel(self.path, header=None)
        header = _find_header(raw, self.signature)
        columns = _header_columns(raw, header)
        date_column = _column_index(columns, "תאריך ערך")
        credit_column = _column_index(columns, "זכות")
        debit_column = _column_index(columns, "חובה")
        description_column = _column_index(columns, "תיאור")
        type_column = _column_index(columns, "סוג פעולה")
        records = []
        for index, row in raw.iloc[header + 1:].iterrows():
            date = _date(row.iloc[date_column])
            description = _clean(row.iloc[description_column])
            credit, debit = _number(row.iloc[credit_column]), _number(row.iloc[debit_column])
            if pd.isna(date) or not description:
                continue
            amount = credit if credit else -debit
            records.append(self.record(index, date, description, amount, _clean(row.iloc[type_column])))
        return records


class DiscountCardParser(CardParser):
    """Discount card workbook, identified by its card-transaction columns."""

    identity_key = "card_ksu"
    signature = ("תאריך עסקה", "שם בית עסק", 'סכום בש"ח', "סוג עסקה")

    @classmethod
    def matches(cls, path: Path) -> bool:
        if path.suffix.lower() != ".xlsx":
            return False
        raw = pd.read_excel(path, header=None)
        try:
            _find_header(raw, cls.signature)
        except ValueError:
            return False
        return True

    def read_records(self) -> list[dict]:
        raw = pd.read_excel(self.path, header=None)
        header = _find_header(raw, self.signature)
        columns = _header_columns(raw, header)
        date_column = _column_index(columns, "תאריך עסקה")
        merchant_column = _column_index(columns, "שם בית עסק")
        amount_column = _column_index(columns, 'סכום בש"ח')
        type_column = _column_index(columns, "סוג עסקה")
        records = []
        for index, row in raw.iloc[header + 1:].iterrows():
            date = _date(row.iloc[date_column])
            description = _clean(row.iloc[merchant_column])
            amount = _number(row.iloc[amount_column])
            if pd.isna(date) or not description or amount == 0:
                continue
            records.append(self.record(index, date, description, -abs(amount), _clean(row.iloc[type_column])))
        return records


class MaxCardParser(CardParser):
    """MAX card workbook, including its domestic and foreign-currency sheets."""

    identity_key = "card_ser"
    signature = ("תאריך עסקה", "שם בית העסק", "קטגוריה", "סכום חיוב")

    @classmethod
    def matches(cls, path: Path) -> bool:
        if path.suffix.lower() != ".xlsx":
            return False
        workbook = pd.ExcelFile(path)
        try:
            for sheet_name in workbook.sheet_names:
                raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
                try:
                    _find_header(raw, cls.signature)
                    return True
                except ValueError:
                    continue
            return False
        finally:
            workbook.close()

    def read_records(self) -> list[dict]:
        records = []
        matched_sheet = False
        workbook = pd.ExcelFile(self.path)
        try:
            for sheet_number, sheet_name in enumerate(workbook.sheet_names):
                raw = pd.read_excel(self.path, sheet_name=sheet_name, header=None)
                try:
                    header = _find_header(raw, self.signature)
                except ValueError:
                    continue
                matched_sheet = True
                columns = _header_columns(raw, header)
                date_column = _column_index(columns, "תאריך עסקה")
                merchant_column = _column_index(columns, "שם בית העסק")
                amount_column = _column_index(columns, "סכום חיוב")
                category_column = _column_index(columns, "קטגוריה")
                identity_key = self.identity_key if sheet_number == 0 else f"{self.identity_key}:{sheet_name}"
                for index, row in raw.iloc[header + 1:].iterrows():
                    date = _date(row.iloc[date_column])
                    description = _clean(row.iloc[merchant_column])
                    amount = _number(row.iloc[amount_column])
                    if pd.isna(date) or not description or amount == 0:
                        continue
                    records.append(self.record(
                        index, date, description, -abs(amount), _clean(row.iloc[category_column]), identity_key,
                    ))
        finally:
            workbook.close()
        if not matched_sheet:
            raise ValueError("Could not find a MAX card table with transaction date, merchant, category, and charge amount")
        return records


SOURCE_FOLDERS = ("bank", "credit_card")

PARSER_TYPES: tuple[type[TransactionParser], ...] = (
    DiscountBankParser,
    BenLeumiBankParser,
    DiscountCardParser,
    MaxCardParser,
)


def identify_parser(path: Path, user: str) -> TransactionParser:
    """Identify an export from its table structure and return its parser."""
    for parser_type in PARSER_TYPES:
        if parser_type.matches(path):
            return parser_type(path, user)
    raise ValueError("Unrecognized export table structure")


def _read_export(path: Path, user: str) -> pd.DataFrame:
    """Identify an export by its columns, then parse it with the matching class."""
    return identify_parser(path, user).parse()


def load_transactions(root: Path) -> tuple[pd.DataFrame, list[str]]:
    frames, warnings = [], []
    users_folder = root / "users"
    if not users_folder.is_dir():
        return _frame([]), ["users: folder not found"]

    for user_folder in sorted(path for path in users_folder.iterdir() if path.is_dir()):
        for folder_name in SOURCE_FOLDERS:
            source_path = user_folder / folder_name
            files = sorted(
                (path for path in source_path.iterdir() if path.suffix.lower() in {".csv", ".xlsx"})
                if source_path.is_dir() else [],
                key=lambda path: path.stat().st_mtime,
            )
            if not files:
                warnings.append(f"users/{user_folder.name}/{folder_name}: no CSV/XLSX exports found")
                continue
            for file in files:
                try:
                    frame = _read_export(file, user_folder.name)
                    frame["table_name"] = file.name
                    frame["_import_file"] = str(file.resolve())
                    frames.append(frame)
                except Exception as error:  # keep dashboard usable when one export is bad
                    warnings.append(f"users/{user_folder.name}/{folder_name}/{file.name}: {error}")
    if not frames:
        return _frame([]), warnings
    combined = pd.concat(frames, ignore_index=True)
    return _frame(deduplicate_transactions(combined).to_dict("records")), warnings
