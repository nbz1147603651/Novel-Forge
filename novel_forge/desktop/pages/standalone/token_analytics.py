"""Qt presentation compatibility for Engine-owned token analytics."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Mapping, cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.token_analytics import (
    _CURRENCY_SYMBOLS,
    _DEFAULT_MODEL_PRICE_KEY,
    _I18N,
    _coerce_float,
    _coerce_int,
    _extract_outline_range,
    _kind_label,
    _model_key,
    _normalize_step_filter,
    _step_label,
    _step_tokens_for_filter,
    collect_project_token_analytics,
    estimate_token_cost_cny,
    load_token_dashboard_prefs,
    save_token_dashboard_prefs,
)
from novel_forge.desktop.pages.document_renderer.incremental import IncrementalDocumentRenderer
from novel_forge.desktop.theme import qcolor_hex, qcolor_rgba, resolve_qcolor
from novel_forge.desktop.widgets import ActionButton, FilterChip


class TokenAnalyticsTab(QWidget):
    """Project-level token analytics view with pricing and currency controls."""

    def __init__(self, project_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_dir = project_dir
        self._prefs = load_token_dashboard_prefs(project_dir)
        self._analytics = collect_project_token_analytics(project_dir)

        self._step_filter_label: QLabel | None = None
        self._step_filter_wrap: QWidget | None = None
        self._step_filter_group: QButtonGroup | None = None
        self._step_filter_chips: dict[str, FilterChip] = {}
        self._currency_label: QLabel | None = None
        self._exchange_label: QLabel | None = None
        self._save_btn: ActionButton | None = None
        self._refresh_btn: ActionButton | None = None
        self._prefs_dirty = False

        self._language: str = "zh"

        self._currency_combo: QComboBox | None = None
        self._model_price_unit_combos: dict[str, QComboBox] = {}
        self._exchange_spin: QDoubleSpinBox | None = None

        self._tabs: QTabWidget | None = None
        self._overview: QTextBrowser | None = None
        self._steps: QTextBrowser | None = None
        self._models: QTextBrowser | None = None
        self._pricing_page: QWidget | None = None
        self._pricing_hint_label: QLabel | None = None
        self._pricing_bulk_title: QLabel | None = None
        self._pricing_rows_holder: QWidget | None = None
        self._pricing_rows_layout: QVBoxLayout | None = None
        self._model_price_row_spins: dict[str, QDoubleSpinBox] = {}

        self._build_ui()
        self._sync_controls_from_prefs()
        self._refresh_views()

    def _txt(self, key: str) -> str:
        return _I18N.get(key, key)

    def _tab_text(self, key: str) -> str:
        tab_map = {
            "tab_overview": "总览",
            "tab_models": "模型明细",
            "tab_steps": "步骤明细",
            "tab_pricing": "设置",
        }
        return tab_map.get(key, self._txt(key))

    @staticmethod
    def _model_key_for_item(item: Mapping[str, Any]) -> str:
        key = str(item.get("key", "") or item.get("label", "") or "").strip()
        if key:
            return key
        return _model_key(item.get("provider"), item.get("model"))

    @staticmethod
    def _split_model_key(key: str) -> tuple[str, str]:
        value = str(key or "").strip()
        if "/" in value:
            provider, model = value.split("/", 1)
            return provider.strip(), model.strip()
        return "", value

    def _model_provider_for_item(self, item: Mapping[str, Any]) -> str:
        provider = str(item.get("provider", "") or "").strip()
        if not provider:
            provider, _model = self._split_model_key(self._model_key_for_item(item))
        return provider or self._txt("provider_unknown")

    def _model_display_name_for_item(self, item: Mapping[str, Any]) -> str:
        display_name = str(item.get("display_name", "") or "").strip()
        if display_name and display_name != self._model_key_for_item(item):
            return display_name
        model_name = str(item.get("model", "") or "").strip()
        if model_name:
            return model_name
        _provider, fallback_model = self._split_model_key(self._model_key_for_item(item))
        return fallback_model or self._model_key_for_item(item)

    def _model_display_compound(self, item: Mapping[str, Any]) -> str:
        return f"{self._model_display_name_for_item(item)} · {self._model_provider_for_item(item)}"

    def _model_display_from_key(self, key: str) -> str:
        provider, model = self._split_model_key(key)
        name = model or key
        provider_display = provider or self._txt("provider_unknown")
        return f"{name} · {provider_display}"

    def _price_for_model_key(self, model_key: str) -> float:
        """Always returns the effective price per MILLION tokens for cost calculation."""
        default_raw = _coerce_float(self._prefs.get("price_per_million"))
        default_unit = str(self._prefs.get("price_unit", "million") or "million")
        overrides = self._prefs.get("model_price_per_million", {})
        unit_overrides = self._prefs.get("model_price_unit", {})
        if model_key != _DEFAULT_MODEL_PRICE_KEY and isinstance(overrides, dict):
            raw = _coerce_float(overrides.get(model_key))
            if raw > 0:
                unit = str(unit_overrides.get(model_key, default_unit) or default_unit)
                return raw * 1000.0 if unit == "thousand" else raw
        return default_raw * 1000.0 if default_unit == "thousand" else default_raw

    def _estimated_cost_cny(self) -> float:
        models = self._analytics.get("models", [])
        if not isinstance(models, list) or not models:
            total_tokens = _coerce_int(self._analytics.get("total_tokens"))
            return estimate_token_cost_cny(
                total_tokens,
                price_per_million=self._price_for_model_key(_DEFAULT_MODEL_PRICE_KEY),
            )
        total = 0.0
        for item in models:
            if not isinstance(item, dict):
                continue
            key = self._model_key_for_item(item)
            tokens = _coerce_int(item.get("tokens"))
            if tokens <= 0:
                continue
            total += estimate_token_cost_cny(
                tokens, price_per_million=self._price_for_model_key(key)
            )
        return total

    def _iter_pricing_row_keys(self) -> list[str]:
        keys: list[str] = [_DEFAULT_MODEL_PRICE_KEY]
        seen: set[str] = {_DEFAULT_MODEL_PRICE_KEY}

        models = self._analytics.get("models", [])
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                key = self._model_key_for_item(item)
                if not key or key in seen:
                    continue
                seen.add(key)
                keys.append(key)

        stored_prices = self._prefs.get("model_price_per_million", {})
        if isinstance(stored_prices, dict):
            for key in sorted(str(raw or "").strip() for raw in stored_prices.keys()):
                if not key or key in seen:
                    continue
                seen.add(key)
                keys.append(key)
        return keys

    def _pricing_label_for_key(self, key: str) -> str:
        if key == _DEFAULT_MODEL_PRICE_KEY:
            return self._txt("model_default")
        models = self._analytics.get("models", [])
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                if self._model_key_for_item(item) == key:
                    return self._model_display_compound(item)
        return self._model_display_from_key(key)

    def _sync_model_price_row_values(self) -> None:
        if not self._model_price_row_spins:
            return
        for key, spin in self._model_price_row_spins.items():
            if key == _DEFAULT_MODEL_PRICE_KEY:
                value = _coerce_float(self._prefs.get("price_per_million"))
            else:
                overrides = self._prefs.get("model_price_per_million", {})
                value = (
                    _coerce_float(overrides.get(key))
                    if isinstance(overrides, dict) and _coerce_float(overrides.get(key)) > 0
                    else _coerce_float(self._prefs.get("price_per_million"))
                )
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _rebuild_pricing_rows(self) -> None:
        if self._pricing_rows_layout is None:
            return
        while self._pricing_rows_layout.count():
            item = self._pricing_rows_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._model_price_row_spins = {}
        self._model_price_unit_combos = {}

        keys = self._iter_pricing_row_keys()
        if len(keys) <= 1:
            empty = QLabel(self._txt("pricing_none"))
            empty.setObjectName("viewerHint")
            empty.setWordWrap(True)
            self._pricing_rows_layout.addWidget(empty)
            self._pricing_rows_layout.addStretch(1)
            return

        unit_options = [
            (self._txt("unit_million"), "million"),
            (self._txt("unit_thousand"), "thousand"),
        ]
        default_unit = str(self._prefs.get("price_unit", "million") or "million")
        unit_overrides = self._prefs.get("model_price_unit", {})

        for key in keys:
            row = QWidget()
            row.setObjectName("tokenPricingRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)
            row_layout.setSpacing(8)

            title = QLabel(self._pricing_label_for_key(key))
            title.setObjectName("tokenPricingRowTitle")
            title.setWordWrap(False)
            row_layout.addWidget(title, 1)

            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(0.0, 1_000_000.0)
            spin.setSingleStep(0.01)
            spin.setMinimumWidth(110)
            if key == _DEFAULT_MODEL_PRICE_KEY:
                spin.setValue(_coerce_float(self._prefs.get("price_per_million")))
            else:
                overrides = self._prefs.get("model_price_per_million", {})
                spin.setValue(
                    _coerce_float(overrides.get(key))
                    if isinstance(overrides, dict) and _coerce_float(overrides.get(key)) > 0
                    else _coerce_float(self._prefs.get("price_per_million"))
                )
            spin.valueChanged.connect(
                lambda value, mk=key: self._on_bulk_model_price_changed(mk, value)
            )
            row_layout.addWidget(spin)

            unit_combo = QComboBox()
            unit_combo.setObjectName("projectSelector")
            unit_combo.setMinimumWidth(80)
            unit_combo.setMaximumWidth(100)
            for label, data in unit_options:
                unit_combo.addItem(label, data)
            current_unit = str(
                unit_overrides.get(key, default_unit)
                if key != _DEFAULT_MODEL_PRICE_KEY
                else default_unit
            )
            idx = unit_combo.findData(current_unit)
            if idx >= 0:
                unit_combo.setCurrentIndex(idx)
            unit_combo.currentIndexChanged.connect(
                lambda _, mk=key, uc=unit_combo: self._on_bulk_unit_changed(mk, uc.currentData())
            )
            row_layout.addWidget(unit_combo)

            self._model_price_row_spins[key] = spin
            self._model_price_unit_combos[key] = unit_combo
            self._pricing_rows_layout.addWidget(row)

        self._pricing_rows_layout.addStretch(1)

    def _on_bulk_model_price_changed(self, model_key: str, value: float) -> None:
        new_value = max(float(value), 0.0)
        if model_key == _DEFAULT_MODEL_PRICE_KEY:
            self._prefs["price_per_million"] = new_value
            self._sync_model_price_row_values()
        else:
            self._prefs.setdefault("model_price_per_million", {})[model_key] = new_value
        self._set_prefs_dirty(True)
        self._refresh_views()

    def _on_bulk_unit_changed(self, model_key: str, unit: str) -> None:
        unit = "thousand" if unit == "thousand" else "million"
        if model_key == _DEFAULT_MODEL_PRICE_KEY:
            old_unit = str(self._prefs.get("price_unit", "million") or "million")
            old_raw = _coerce_float(self._prefs.get("price_per_million"))
            self._prefs["price_unit"] = unit
        else:
            unit_overrides = self._prefs.get("model_price_unit", {})
            old_unit = str(
                unit_overrides.get(model_key, self._prefs.get("price_unit", "million")) or "million"
            )
            overrides = self._prefs.get("model_price_per_million", {})
            old_raw = (
                _coerce_float(overrides.get(model_key)) if isinstance(overrides, dict) else 0.0
            )
            if old_raw <= 0:
                old_raw = _coerce_float(self._prefs.get("price_per_million"))
            self._prefs.setdefault("model_price_unit", {})[model_key] = unit
        # convert displayed spin value to new unit
        if old_unit != unit:
            new_display = old_raw / 1000.0 if unit == "thousand" else old_raw * 1000.0
            if model_key == _DEFAULT_MODEL_PRICE_KEY:
                self._prefs["price_per_million"] = new_display
            else:
                self._prefs.setdefault("model_price_per_million", {})[model_key] = new_display
            spin = self._model_price_row_spins.get(model_key)
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(new_display)
                spin.blockSignals(False)
        self._set_prefs_dirty(True)
        self._refresh_views()

    def _step_filter_label_for_key(self, filter_key: str) -> str:
        key = _normalize_step_filter(filter_key)
        mapping = {
            "all": self._txt("filter_all"),
            "init": self._txt("filter_init"),
            "chapter": self._txt("filter_chapter"),
            "repair": self._txt("filter_repair"),
        }
        return mapping.get(key, mapping["all"])

    def _sync_step_filter_selector(self) -> None:
        if not self._step_filter_chips:
            return
        selected = _normalize_step_filter(self._prefs.get("step_waterfall_filter", "all"))
        if selected not in self._step_filter_chips:
            self._prefs["step_waterfall_filter"] = "all"
            selected = "all"
        for key, chip in self._step_filter_chips.items():
            chip.blockSignals(True)
            chip.setText(self._step_filter_label_for_key(key))
            chip.setChecked(key == selected)
            chip.blockSignals(False)

    def _set_prefs_dirty(self, dirty: bool = True) -> None:
        self._prefs_dirty = bool(dirty)
        self._refresh_save_button_state()

    def _refresh_save_button_state(self) -> None:
        if self._save_btn is None:
            return
        if self._prefs_dirty:
            self._save_btn.setEnabled(True)
            self._save_btn.setText(self._txt("save_dirty"))
            self._save_btn.setProperty("variant", "primary")
        else:
            self._save_btn.setEnabled(False)
            self._save_btn.setText(self._txt("save"))
            self._save_btn.setProperty("variant", "secondary")
        self._save_btn.style().unpolish(self._save_btn)
        self._save_btn.style().polish(self._save_btn)

    def has_unsaved_changes(self) -> bool:
        return bool(self._prefs_dirty)

    def save_pending_changes(self) -> bool:
        if not self._prefs_dirty:
            return True
        self._save_prefs()
        self._set_prefs_dirty(False)
        return True

    def unsaved_changes_description(self) -> str:
        return "- Token 追踪页有未保存设置"

    def _build_ui(self) -> None:
        self.setObjectName("tokenAnalyticsTab")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(4)

        self._save_btn = ActionButton("", variant="primary")
        self._save_btn.clicked.connect(self._on_save_clicked)

        self._refresh_btn = ActionButton("", variant="secondary")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("tokenAnalyticsTabs")
        self._tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tab_bar = self._tabs.tabBar()
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(True)
        self._overview = QTextBrowser()
        self._steps = QTextBrowser()
        self._models = QTextBrowser()
        for browser in (self._overview, self._steps, self._models):
            browser.setOpenExternalLinks(False)
            browser.setReadOnly(True)
            browser.setFrameShape(browser.Shape.NoFrame)
        self._overview_renderer = IncrementalDocumentRenderer(self._overview)
        self._steps_renderer = IncrementalDocumentRenderer(self._steps)
        self._models_renderer = IncrementalDocumentRenderer(self._models)

        self._pricing_page = QWidget()
        self._pricing_page.setObjectName("tokenPricingPage")
        pricing_layout = QVBoxLayout(self._pricing_page)
        pricing_layout.setContentsMargins(10, 10, 10, 10)
        pricing_layout.setSpacing(8)

        self._pricing_hint_label = QLabel()
        self._pricing_hint_label.setObjectName("tokenPricingHint")
        self._pricing_hint_label.setWordWrap(True)
        pricing_layout.addWidget(self._pricing_hint_label)

        general_row = QGridLayout()
        general_row.setContentsMargins(0, 0, 0, 0)
        general_row.setHorizontalSpacing(8)
        general_row.setVerticalSpacing(6)

        self._currency_label = QLabel()
        self._currency_label.setObjectName("viewerFilterLabel")
        general_row.addWidget(self._currency_label, 0, 0)

        self._currency_combo = QComboBox()
        self._currency_combo.setObjectName("projectSelector")
        self._currency_combo.setMinimumWidth(120)
        for code in _CURRENCY_SYMBOLS:
            self._currency_combo.addItem(code, code)
        self._currency_combo.currentIndexChanged.connect(self._on_currency_changed)
        general_row.addWidget(self._currency_combo, 0, 1)

        self._exchange_label = QLabel()
        self._exchange_label.setObjectName("viewerFilterLabel")
        general_row.addWidget(self._exchange_label, 0, 2)

        self._exchange_spin = QDoubleSpinBox()
        self._exchange_spin.setObjectName("tokenExchangeSpin")
        self._exchange_spin.setDecimals(6)
        self._exchange_spin.setRange(0.000001, 1000000.0)
        self._exchange_spin.setSingleStep(0.01)
        self._exchange_spin.setMinimumWidth(140)
        self._exchange_spin.valueChanged.connect(self._on_exchange_changed)
        general_row.addWidget(self._exchange_spin, 0, 3)
        general_row.setColumnStretch(1, 1)
        general_row.setColumnStretch(3, 1)
        pricing_layout.addLayout(general_row)

        self._pricing_bulk_title = QLabel()
        self._pricing_bulk_title.setObjectName("tokenPricingSectionTitle")
        pricing_layout.addWidget(self._pricing_bulk_title)

        pricing_scroll = QScrollArea()
        pricing_scroll.setWidgetResizable(True)
        pricing_scroll.setFrameShape(pricing_scroll.Shape.NoFrame)
        self._pricing_rows_holder = QWidget()
        self._pricing_rows_layout = QVBoxLayout(self._pricing_rows_holder)
        self._pricing_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._pricing_rows_layout.setSpacing(6)
        pricing_scroll.setWidget(self._pricing_rows_holder)
        pricing_layout.addWidget(pricing_scroll, 1)

        # ── Step filter bar ────────────────────────────────────
        self._step_filter_wrap = QWidget()
        self._step_filter_wrap.setObjectName("tokenStepFilterWrap")
        _steps_outer = QVBoxLayout(self._step_filter_wrap)
        _steps_outer.setContentsMargins(0, 0, 0, 0)
        _steps_outer.setSpacing(0)

        _filter_bar_widget = QWidget()
        _filter_bar_widget.setObjectName("tokenFilterBar")
        _filter_bar_layout = QHBoxLayout(_filter_bar_widget)
        _filter_bar_layout.setContentsMargins(8, 4, 8, 4)
        _filter_bar_layout.setSpacing(6)

        self._step_filter_group = QButtonGroup(self)
        self._step_filter_group.setExclusive(True)
        for _fkey in ("all", "init", "chapter", "repair"):
            _chip = FilterChip(self._txt(f"filter_{_fkey}"), active=(_fkey == "all"))
            self._step_filter_chips[_fkey] = _chip
            self._step_filter_group.addButton(_chip)
            _filter_bar_layout.addWidget(_chip)
            _chip.toggled.connect(
                lambda checked, k=_fkey: self._on_step_filter_chip_toggled(k, checked)
            )
        _filter_bar_layout.addStretch(1)

        _steps_outer.addWidget(_filter_bar_widget)
        _steps_outer.addWidget(self._steps, 1)

        corner_host = QWidget(self._tabs)
        corner_layout = QHBoxLayout(corner_host)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(6)
        corner_layout.addWidget(self._save_btn)
        corner_layout.addWidget(self._refresh_btn)
        self._tabs.setCornerWidget(corner_host, Qt.Corner.TopRightCorner)

        self._tabs.addTab(self._overview, "")
        self._tabs.addTab(self._models, "")
        self._tabs.addTab(self._step_filter_wrap, "")
        self._tabs.addTab(self._pricing_page, "")
        layout.addWidget(self._tabs, 1)

    def _sync_controls_from_prefs(self) -> None:
        currency_combo = self._currency_combo
        exchange_spin = self._exchange_spin
        tabs = self._tabs
        currency_label = self._currency_label
        exchange_label = self._exchange_label
        save_btn = self._save_btn
        refresh_btn = self._refresh_btn
        pricing_hint_label = self._pricing_hint_label
        pricing_bulk_title = self._pricing_bulk_title
        if (
            currency_combo is None
            or exchange_spin is None
            or tabs is None
            or currency_label is None
            or exchange_label is None
            or save_btn is None
            or refresh_btn is None
            or pricing_hint_label is None
            or pricing_bulk_title is None
        ):
            return

        currency = str(self._prefs.get("currency", "CNY") or "CNY").upper()
        if currency not in _CURRENCY_SYMBOLS:
            currency = "CNY"
            self._prefs["currency"] = currency

        currency_combo.blockSignals(True)
        cidx = currency_combo.findData(currency)
        if cidx >= 0:
            currency_combo.setCurrentIndex(cidx)
        currency_combo.blockSignals(False)

        rate = _coerce_float(self._prefs.get("exchange_rates", {}).get(currency))
        if currency == "CNY":
            rate = 1.0
        if rate <= 0:
            rate = 1.0

        exchange_spin.blockSignals(True)
        exchange_spin.setValue(rate)
        exchange_spin.setEnabled(currency != "CNY")
        exchange_spin.blockSignals(False)

        currency_label.setText(self._txt("currency"))
        exchange_label.setText(self._txt("exchange"))
        save_btn.setText(self._txt("save"))
        refresh_btn.setText(self._txt("refresh"))
        pricing_hint_label.setText(self._txt("pricing_hint"))
        pricing_bulk_title.setText(self._txt("pricing_all_models"))
        self._refresh_save_button_state()
        self._rebuild_pricing_rows()

        tabs.setTabText(0, self._tab_text("tab_overview"))
        tabs.setTabText(1, self._tab_text("tab_models"))
        tabs.setTabText(2, self._tab_text("tab_steps"))
        tabs.setTabText(3, self._tab_text("tab_pricing"))

    def _save_prefs(self) -> None:
        save_token_dashboard_prefs(self._project_dir, self._prefs)

    def _on_currency_changed(self, index: int) -> None:
        if self._currency_combo is None or index < 0:
            return
        currency = str(self._currency_combo.itemData(index) or "CNY")
        self._prefs["currency"] = currency
        if currency == "CNY":
            self._prefs.setdefault("exchange_rates", {})["CNY"] = 1.0
        self._set_prefs_dirty(True)
        self._sync_controls_from_prefs()
        self._refresh_views()

    def _on_exchange_changed(self, value: float) -> None:
        currency = str(self._prefs.get("currency", "CNY") or "CNY")
        if currency == "CNY":
            return
        self._prefs.setdefault("exchange_rates", {})[currency] = max(float(value), 0.000001)
        self._set_prefs_dirty(True)
        self._refresh_views()

    def _on_step_filter_chip_toggled(self, key: str, checked: bool) -> None:
        if not checked:
            return
        normalized = _normalize_step_filter(key)
        if normalized == _normalize_step_filter(self._prefs.get("step_waterfall_filter", "all")):
            self._sync_step_filter_selector()
            return
        self._prefs["step_waterfall_filter"] = normalized
        self._set_prefs_dirty(True)
        self._sync_step_filter_selector()
        self._refresh_views()

    def _on_save_clicked(self) -> None:
        self.save_pending_changes()

    def _on_refresh_clicked(self) -> None:
        self._analytics = collect_project_token_analytics(self._project_dir)
        self._sync_controls_from_prefs()
        self._sync_model_price_row_values()
        self._refresh_views()

    def _refresh_views(self) -> None:
        overview = self._overview
        steps = self._steps
        models = self._models
        if overview is None or steps is None or models is None:
            return
        self._overview_renderer.update_content(self._render_overview_html())
        self._steps_renderer.update_content(self._render_steps_html())
        self._models_renderer.update_content(self._render_models_html())

    def _cost_context(self) -> tuple[float, str, str, float]:
        cost_cny = self._estimated_cost_cny()

        currency = str(self._prefs.get("currency", "CNY") or "CNY").upper()
        if currency not in _CURRENCY_SYMBOLS:
            currency = "CNY"
        rate = _coerce_float(self._prefs.get("exchange_rates", {}).get(currency))
        if currency == "CNY":
            rate = 1.0
        if rate <= 0:
            rate = 1.0

        converted = cost_cny * rate
        symbol = _CURRENCY_SYMBOLS.get(currency, "")
        return converted, symbol, currency, rate

    @staticmethod
    def _chart_palette() -> list[str]:
        return [
            "#b65634",
            "#d9875d",
            "#8f9e68",
            "#5f8b5a",
            "#4c729c",
            "#8b6a94",
            "#c57f4d",
            "#7a8fbe",
        ]

    @staticmethod
    def _encode_png_data_url(image: QImage) -> str:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            return ""
        image.save(buffer, cast(Any, "PNG"))
        encoded = bytes(cast(Any, byte_array.toBase64())).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _render_model_cost_ring_image(
        self, slices: list[tuple[str, float]], *, total_cost_cny: float
    ) -> str:
        if not slices:
            return ""
        # I-8: devicePixelRatio 感知位图 — Retina 屏渲染更清晰
        dpr = float(self.devicePixelRatioF() or 1.0)
        size_logical = 176
        pen_width = 24
        size_px = int(round(size_logical * dpr))
        image = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        ring_margin = float((pen_width // 2) + 4)
        # painter 坐标系使用 logical 像素（QImage 自动按 dpr 缩放）
        rect = QRectF(
            ring_margin,
            ring_margin,
            float(size_logical) - ring_margin * 2.0,
            float(size_logical) - ring_margin * 2.0,
        )

        base_pen = QPen(resolve_qcolor("bg.control"))
        base_pen.setWidth(pen_width)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(base_pen)
        painter.drawArc(rect, 0, 360 * 16)

        palette = self._chart_palette()
        start_angle = 90 * 16
        for index, (_label, pct) in enumerate(slices):
            if pct <= 0:
                continue
            span_angle = int(round(360.0 * 16.0 * (pct / 100.0)))
            if span_angle <= 0:
                continue
            pen = QPen(QColor(palette[index % len(palette)]))
            pen.setWidth(pen_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, start_angle, -span_angle)
            start_angle -= span_angle

        inner_diameter = float(size_logical) - 2.0 * (ring_margin + pen_width * 0.78)
        inner_rect = QRectF(
            (float(size_logical) - inner_diameter) / 2.0,
            (float(size_logical) - inner_diameter) / 2.0,
            inner_diameter,
            inner_diameter,
        )
        painter.setPen(QPen(resolve_qcolor("separator"), 1))
        painter.setBrush(resolve_qcolor("bg.surface.elevated"))
        painter.drawEllipse(inner_rect)

        total_text = f"{total_cost_cny:,.3f}"
        value_font_size = 17 if len(total_text) <= 7 else 15
        value_font = QFont("PingFang SC", value_font_size)
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(resolve_qcolor("accent.primary"))
        value_rect = QRectF(
            inner_rect.left(),
            inner_rect.top() + inner_rect.height() * 0.20,
            inner_rect.width(),
            inner_rect.height() * 0.45,
        )
        painter.drawText(
            value_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            total_text,
        )

        unit_font = QFont("PingFang SC", 10)
        painter.setFont(unit_font)
        painter.setPen(resolve_qcolor("text.muted"))
        unit_rect = QRectF(
            inner_rect.left(),
            inner_rect.top() + inner_rect.height() * 0.56,
            inner_rect.width(),
            inner_rect.height() * 0.25,
        )
        painter.drawText(
            unit_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "CNY",
        )

        painter.end()
        return self._encode_png_data_url(image)

    def _render_model_cost_ring(self, models: list[dict[str, Any]]) -> str:
        rows: list[tuple[str, str, str, float, float]] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            model_key = self._model_key_for_item(item)
            display_name = self._model_display_name_for_item(item)
            provider = self._model_provider_for_item(item)
            tokens = _coerce_int(item.get("tokens"))
            if tokens <= 0:
                continue
            price_per_million = self._price_for_model_key(model_key)
            est_cost_cny = estimate_token_cost_cny(tokens, price_per_million=price_per_million)
            if est_cost_cny <= 0:
                continue
            rows.append((display_name, provider, model_key, price_per_million, est_cost_cny))
        if not rows:
            return ""

        rows.sort(key=lambda item: item[4], reverse=True)
        if len(rows) > 7:
            remaining = sum(item[4] for item in rows[6:])
            rows = rows[:6] + [
                (
                    self._txt("other_bucket"),
                    self._txt("provider_unknown"),
                    self._txt("other_bucket"),
                    0.0,
                    remaining,
                )
            ]

        total_cost = sum(item[4] for item in rows)
        if total_cost <= 0:
            return ""

        palette = self._chart_palette()
        legend_rows: list[str] = []
        ring_slices: list[tuple[str, float]] = []
        for idx, (display_name, provider, model_key, price_per_million, cost_cny) in enumerate(
            rows
        ):
            _ = model_key
            color = palette[idx % len(palette)]
            pct = max((cost_cny / total_cost) * 100.0, 0.0)
            if pct <= 0:
                continue
            ring_slices.append((display_name, pct))
            tooltip_text = (
                f"{display_name} · {provider} | "
                f"{self._txt('col_price')}: {price_per_million:,.4f} CNY | "
                f"{self._txt('col_cost')}: {cost_cny:,.4f} CNY | "
                f"{self._txt('col_share')}: {pct:.1f}%"
            )
            legend_rows.append(
                f"<div class='ring-item' title=\"{escape(tooltip_text)}\">"
                "<span class='ring-dot-cell'>"
                f"<span class='ring-dot' style='background:{color}'></span>"
                "</span>"
                "<span class='ring-text-cell'>"
                f"<div class='ring-title'>{escape(display_name)}</div>"
                f"<div class='row-meta'>{escape(provider)}</div>"
                "</span>"
                f"<span class='ring-value-cell'>{cost_cny:,.4f} CNY · {(cost_cny / total_cost):.1%}</span>"
                "</div>"
            )

        ring_data_url = self._render_model_cost_ring_image(ring_slices, total_cost_cny=total_cost)
        ring_img_html = (
            f"<img class='ring-image' src='{ring_data_url}' alt='model-cost-ring'/>"
            if ring_data_url
            else "<div class='ring-image ring-fallback'></div>"
        )

        return (
            "<div class='chart-panel'>"
            f"<h3>{self._txt('model_cost_ring')}</h3>"
            f"<div class='chart-hint'>{self._txt('model_cost_ring_hint')}</div>"
            f"<div class='chart-hint subtle'>{self._txt('hover_hint')}</div>"
            "<div class='ring-wrap'>"
            "<div class='ring-frame'>"
            f"{ring_img_html}"
            "</div>"
            f"<div class='ring-legend'>{''.join(legend_rows)}</div>"
            "</div>"
            "</div>"
        )

    def _render_step_waterfall(self, steps: list[dict[str, Any]], *, filter_key: str) -> str:
        step_rows: list[tuple[str, int]] = []
        for item in steps:
            if not isinstance(item, dict):
                continue
            step_key = str(item.get("step", "") or "")
            tokens = _step_tokens_for_filter(item, filter_key)
            if not step_key or tokens <= 0:
                continue
            step_rows.append((_step_label(step_key, self._language), tokens))
        if not step_rows:
            return (
                "<div class='chart-panel'>"
                f"<h3>{self._txt('step_waterfall')}</h3>"
                f"<div class='chart-hint'>{self._txt('step_waterfall_hint')}</div>"
                f"<div class='empty'>{self._txt('step_waterfall_empty')}</div>"
                "</div>"
            )

        total_step_tokens = sum(tokens for _, tokens in step_rows)
        if total_step_tokens <= 0:
            return ""

        display_rows = step_rows[:10]
        shown_tokens = sum(tokens for _, tokens in display_rows)
        remainder = total_step_tokens - shown_tokens
        if remainder > 0:
            display_rows.append((self._txt("other_bucket"), remainder))

        palette = self._chart_palette()
        running = 0
        row_html: list[str] = []
        for idx, (label, tokens) in enumerate(display_rows):
            start = running
            running += tokens
            start_pct = (start / total_step_tokens) * 100.0
            end_pct = (running / total_step_tokens) * 100.0
            width_pct = max(end_pct - start_pct, 1.1)
            if start_pct + width_pct > 100.0:
                width_pct = 100.0 - start_pct
            color = palette[idx % len(palette)]
            row_html.append(
                "<div class='wf-row'>"
                f"<div class='wf-name' title=\"{escape(label)}\">{escape(label)}</div>"
                "<div class='wf-track'>"
                f"<div class='wf-seg' title=\"{escape(label)} | +{tokens:,} | {end_pct:.1f}%\" "
                f"style='left:{start_pct:.2f}%;width:{width_pct:.2f}%;background:{color};'></div>"
                "</div>"
                f"<div class='wf-add'>+{tokens:,}</div>"
                f"<div class='wf-cum'>{end_pct:.1f}%</div>"
                "</div>"
            )

        filter_active = _normalize_step_filter(filter_key) != "all"
        filter_label_html = ""
        if filter_active:
            label_text = self._txt("step_waterfall_filtered_by").format(
                label=self._step_filter_label_for_key(filter_key)
            )
            filter_label_html = f"<div class='filter-active-hint'>{escape(label_text)}</div>"
        return (
            "<div class='chart-panel'>"
            f"<h3>{self._txt('step_waterfall')}</h3>"
            f"<div class='chart-hint'>{self._txt('step_waterfall_hint')}</div>"
            f"{filter_label_html}"
            "<div class='wf-axis'><span>0%</span><span>100%</span></div>"
            f"<div class='wf-rows'>{''.join(row_html)}</div>"
            "</div>"
        )

    def _status_label(self, status: str) -> str:
        status_map = {
            "success": self._txt("status_success"),
            "error": self._txt("status_error"),
            "running": self._txt("status_running"),
        }
        return status_map.get(status, self._txt("status_other"))

    def _render_overview_html(self) -> str:
        runs = self._analytics.get("runs", [])
        if not isinstance(runs, list) or not runs:
            return self._wrap_html(f"<div class='empty'>{self._txt('no_data')}</div>")

        total_tokens = _coerce_int(self._analytics.get("total_tokens"))
        prompt_tokens = _coerce_int(self._analytics.get("total_prompt_tokens"))
        completion_tokens = _coerce_int(self._analytics.get("total_completion_tokens"))
        unattributed_tokens = _coerce_int(self._analytics.get("unattributed_tokens"))
        total_call_count = _coerce_int(self._analytics.get("total_call_count"))
        run_count = _coerce_int(self._analytics.get("run_count"))
        step_count = _coerce_int(self._analytics.get("step_count"))
        logged_cost_usd = _coerce_float(self._analytics.get("logged_cost_usd"))
        recovered_tokens = _coerce_int(self._analytics.get("recovered_tokens"))
        recovered_run_count = _coerce_int(self._analytics.get("recovered_run_count"))
        reconciled_tokens = _coerce_int(self._analytics.get("reconciled_tokens"))
        reconciled_run_count = _coerce_int(self._analytics.get("reconciled_run_count"))
        project_started_at = str(self._analytics.get("project_started_at", "") or "")

        cost_value, cost_symbol, cost_currency, _rate = self._cost_context()

        cards = [
            (self._txt("metric_tokens"), f"{total_tokens:,}"),
            (self._txt("metric_prompt"), f"{prompt_tokens:,}"),
            (self._txt("metric_completion"), f"{completion_tokens:,}"),
            (self._txt("metric_calls"), f"{total_call_count:,}"),
            (self._txt("metric_runs"), str(run_count)),
            (self._txt("metric_cost"), f"{cost_symbol}{cost_value:,.2f} {cost_currency}"),
            (self._txt("metric_logged_cost"), f"${logged_cost_usd:,.4f}"),
            (
                self._txt("metric_unattributed"),
                f"{unattributed_tokens:,}" if unattributed_tokens else "0",
            ),
        ]
        cards_html = "".join(
            (
                "<div class='metric-card'>"
                f"<div class='metric-label'>{label}</div>"
                f"<div class='metric-value'>{value}</div>"
                "</div>"
            )
            for label, value in cards
        )

        split_coverage = (
            min(total_tokens, prompt_tokens + completion_tokens) / total_tokens
            if total_tokens > 0
            else 1.0
        )
        head = (
            f"<div class='subtitle'>{self._txt('from_start')}：{project_started_at or '-'}"
            f" · {self._txt('metric_steps')} {step_count:,}"
            f" · {self._txt('split_coverage').format(coverage=split_coverage)}</div>"
        )
        recovered_hint = ""
        if recovered_tokens > 0:
            recovered_hint = (
                "<div class='recovery-hint'>"
                f"{self._txt('recovered_hint')} · {recovered_run_count} runs · {recovered_tokens:,} tokens"
                "</div>"
            )
        if reconciled_tokens > 0:
            recovered_hint += (
                "<div class='recovery-hint reconciled'>"
                f"{self._txt('reconciled_hint')} · {reconciled_run_count} runs "
                f"· 差额 {reconciled_tokens:,} tokens"
                "</div>"
            )

        status_tokens: dict[str, int] = {"success": 0, "error": 0, "running": 0, "other": 0}
        for item in runs:
            if not isinstance(item, dict):
                continue
            status_key = str(item.get("status", "") or "")
            if status_key not in status_tokens:
                status_key = "other"
            status_tokens[status_key] += _coerce_int(item.get("tokens"))
        mix_total = max(sum(status_tokens.values()), 1)
        mix_segments = []
        mix_legend = []
        color_map = {
            "success": "success",
            "error": "error",
            "running": "running",
            "other": "other",
        }
        for key in ("success", "error", "running", "other"):
            value = status_tokens[key]
            if value <= 0:
                continue
            pct = (value / mix_total) * 100.0
            mix_segments.append(
                f"<div class='mix-seg {color_map[key]}' style='width:{pct:.2f}%'></div>"
            )
            mix_legend.append(
                "<div class='mix-item'>"
                f"<span class='dot {color_map[key]}'></span>"
                f"{self._status_label(key)} · {value:,} ({pct:.1f}%)"
                "</div>"
            )
        mix_html = ""
        if mix_segments:
            mix_html = (
                "<div class='status-panel'>"
                f"<h3>{self._txt('status_mix')}</h3>"
                f"<div class='mix-track'>{''.join(mix_segments)}</div>"
                f"<div class='mix-legend'>{''.join(mix_legend)}</div>"
                "</div>"
            )

        rows_html = ""
        for item in runs[-30:][::-1]:
            if not isinstance(item, dict):
                continue
            chapter = _coerce_int(item.get("chapter"))
            source = str(item.get("token_source", "") or "")
            source_key = {
                "model_calls": self._txt("source_fallback"),
                "verified": self._txt("source_verified"),
                "reconciled": self._txt("source_reconciled"),
            }.get(source, self._txt("source_trace"))
            task_label = _kind_label(str(item.get("kind", "")), self._language)
            rows_html += (
                "<tr>"
                f"<td>{item.get('started_at', '')}</td>"
                f"<td>{task_label}<div class='row-meta'>{source_key}</div></td>"
                f"<td>{chapter if chapter > 0 else '-'}</td>"
                f"<td>{_coerce_int(item.get('tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('prompt_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('completion_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('calls')):,}</td>"
                f"<td>{_coerce_int(item.get('step_count'))}</td>"
                f"<td>{self._status_label(str(item.get('status', '')))}</td>"
                "</tr>"
            )

        table_html = (
            f"<h3>{self._txt('run_history')}</h3>"
            "<table>"
            "<thead><tr>"
            f"<th>{self._txt('col_started')}</th>"
            f"<th>{self._txt('col_kind')}</th>"
            f"<th>{self._txt('col_chapter')}</th>"
            f"<th>{self._txt('col_tokens')}</th>"
            f"<th>{self._txt('col_prompt')}</th>"
            f"<th>{self._txt('col_completion')}</th>"
            f"<th>{self._txt('col_calls')}</th>"
            f"<th>{self._txt('col_steps')}</th>"
            f"<th>{self._txt('col_status')}</th>"
            "</tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table>"
        )

        return self._wrap_html(
            (
                f"<h2>{self._txt('title')}</h2>"
                f"{head}"
                f"{recovered_hint}"
                f"<div class='metrics'>{cards_html}</div>"
                f"{mix_html}"
                f"{table_html}"
            )
        )

    def _render_steps_html(self) -> str:
        steps = self._analytics.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return self._wrap_html(f"<div class='empty'>{self._txt('no_data')}</div>")

        ranges: set[tuple[int, int]] = set()
        for item in steps:
            if not isinstance(item, dict):
                continue
            step_key = str(item.get("step", "") or "")
            range_pair = _extract_outline_range(step_key)
            if range_pair is not None:
                ranges.add(range_pair)
        scope_html = ""
        if ranges:
            start = min(pair[0] for pair in ranges)
            end = max(pair[1] for pair in ranges)
            scope_html = (
                "<div class='scope-hint'>"
                f"<div class='scope-title'>{self._txt('outline_scope_title')}</div>"
                f"<div>{self._txt('outline_scope_desc')}</div>"
                "<div class='scope-value'>"
                f"{self._txt('outline_scope_value').format(start=start, end=end, segments=len(ranges))}"
                "</div>"
                "</div>"
            )
        filter_key = _normalize_step_filter(self._prefs.get("step_waterfall_filter", "all"))
        waterfall_html = self._render_step_waterfall(
            [item for item in steps if isinstance(item, dict)],
            filter_key=filter_key,
        )

        total_tokens = max(_coerce_int(self._analytics.get("total_tokens")), 1)
        max_tokens = max(
            (_coerce_int(item.get("tokens")) for item in steps if isinstance(item, dict)), default=1
        )

        bars = []
        rows = []
        spotlight_cards = []
        rank = 0
        for item in steps[:40]:
            if not isinstance(item, dict):
                continue
            step_key = str(item.get("step", "") or "")
            tokens = _coerce_int(item.get("tokens"))
            if tokens <= 0:
                continue
            rank += 1
            share = tokens / total_tokens
            width = max(2, int((tokens / max_tokens) * 100))
            label = _step_label(step_key, self._language)
            if rank <= 3:
                spotlight_cards.append(
                    "<div class='spotlight-card'>"
                    f"<div class='spotlight-rank'>#{rank}</div>"
                    f"<div class='spotlight-name'>{label}</div>"
                    f"<div class='spotlight-value'>{tokens:,}</div>"
                    "</div>"
                )
            bars.append(
                (
                    "<div class='bar-row'>"
                    f"<div class='bar-name'>{label}</div>"
                    f"<div class='bar-track'><div class='bar-fill' style='width:{width}%;'></div></div>"
                    f"<div class='bar-val'>{tokens:,}</div>"
                    "</div>"
                )
            )
            rows.append(
                "<tr>"
                f"<td>{label}</td>"
                f"<td>{tokens:,}</td>"
                f"<td>{_coerce_int(item.get('prompt_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('completion_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('calls'))}</td>"
                f"<td>{share:.1%}</td>"
                "</tr>"
            )

        table_html = (
            "<table><thead><tr>"
            f"<th>{self._txt('col_step')}</th>"
            f"<th>{self._txt('col_tokens')}</th>"
            f"<th>{self._txt('col_prompt')}</th>"
            f"<th>{self._txt('col_completion')}</th>"
            f"<th>{self._txt('col_calls')}</th>"
            f"<th>{self._txt('col_share')}</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

        return self._wrap_html(
            (
                f"<h2>{self._txt('step_dist')}</h2>"
                f"{scope_html}"
                f"{waterfall_html}"
                f"<div class='spotlight'>{''.join(spotlight_cards)}</div>"
                f"<div class='bars'>{''.join(bars)}</div>"
                f"{table_html}"
            )
        )

    def _render_models_html(self) -> str:
        models = self._analytics.get("models", [])
        if not isinstance(models, list) or not models:
            return self._wrap_html(f"<div class='empty'>{self._txt('no_data')}</div>")

        total_tokens = max(_coerce_int(self._analytics.get("total_tokens")), 1)
        max_tokens = max(
            (_coerce_int(item.get("tokens")) for item in models if isinstance(item, dict)),
            default=1,
        )
        ring_html = self._render_model_cost_ring(
            [item for item in models if isinstance(item, dict)]
        )

        bars = []
        rows = []
        spotlight_cards = []
        rank = 0
        for item in models[:40]:
            if not isinstance(item, dict):
                continue
            model_key = self._model_key_for_item(item)
            display_name = self._model_display_name_for_item(item)
            provider = self._model_provider_for_item(item)
            tokens = _coerce_int(item.get("tokens"))
            if tokens <= 0:
                continue
            rank += 1
            share = tokens / total_tokens
            width = max(2, int((tokens / max_tokens) * 100))
            price_per_million = self._price_for_model_key(model_key)
            est_cost_cny = estimate_token_cost_cny(tokens, price_per_million=price_per_million)
            if rank <= 3:
                spotlight_cards.append(
                    "<div class='spotlight-card model'>"
                    f"<div class='spotlight-rank'>#{rank}</div>"
                    f"<div class='spotlight-name'>{display_name}</div>"
                    f"<div class='row-meta'>{provider}</div>"
                    f"<div class='spotlight-value'>{tokens:,}</div>"
                    "</div>"
                )
            bars.append(
                (
                    "<div class='bar-row'>"
                    f"<div class='bar-name'>{display_name}<div class='row-meta'>{provider}</div></div>"
                    f"<div class='bar-track'><div class='bar-fill model' style='width:{width}%;'></div></div>"
                    f"<div class='bar-val'>{tokens:,}</div>"
                    "</div>"
                )
            )
            rows.append(
                "<tr>"
                f"<td>{display_name}<div class='row-meta'>{provider}</div></td>"
                f"<td>{tokens:,}</td>"
                f"<td>{_coerce_int(item.get('prompt_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('completion_tokens')):,}</td>"
                f"<td>{_coerce_int(item.get('calls'))}</td>"
                f"<td>{price_per_million:,.4f}</td>"
                f"<td>{est_cost_cny:,.4f}</td>"
                f"<td>{share:.1%}</td>"
                "</tr>"
            )

        table_html = (
            "<table><thead><tr>"
            f"<th>{self._txt('model')}</th>"
            f"<th>{self._txt('col_tokens')}</th>"
            f"<th>{self._txt('col_prompt')}</th>"
            f"<th>{self._txt('col_completion')}</th>"
            f"<th>{self._txt('col_calls')}</th>"
            f"<th>{self._txt('col_price')}</th>"
            f"<th>{self._txt('col_cost')}</th>"
            f"<th>{self._txt('col_share')}</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

        return self._wrap_html(
            (
                f"<h2>{self._txt('model_dist')}</h2>"
                f"{ring_html}"
                f"<div class='spotlight'>{''.join(spotlight_cards)}</div>"
                f"<div class='bars'>{''.join(bars)}</div>"
                f"{table_html}"
            )
        )

    @staticmethod
    def _wrap_html(body: str) -> str:
        _h = qcolor_hex
        _r = qcolor_rgba
        css = (
        "body {"
        '    font-family: "PingFang SC", "Helvetica Neue", "Arial";'
        "    font-size: 12px;"
        f"    color: {_h('text.artifact')};"
        f"    background: {_h('bg.panel')};"
        "    margin: 0;"
        "    padding: 8px 10px 10px 10px;"
        "}"
        "h2 {"
        "    margin: 0 0 8px 0;"
        "    font-size: 17px;"
        "    font-weight: 700;"
        f"    color: {_h('text.guidance')};"
        "    padding-left: 8px;"
        f"    border-left: 3px solid {_h('accent.primary')};"
        "}"
        "h3 {"
        "    margin: 10px 0 6px 0;"
        "    font-size: 13px;"
        f"    color: {_h('text.body')};"
        "}"
        ".subtitle {"
        f"    color: {_h('text.muted')};"
        "    margin-bottom: 8px;"
        "    font-size: 11px;"
        "}"
        ".recovery-hint {"
        "    margin-bottom: 8px;"
        "    padding: 6px 8px;"
        f"    background: {_h('status.warning.bg')};"
        f"    border: 1px solid {_h('status.warning.border')};"
        "    border-radius: 8px;"
        f"    color: {_h('status.warning.text')};"
        "    font-size: 11px;"
        "}"
        ".scope-hint {"
        "    margin-bottom: 8px;"
        "    padding: 6px 8px;"
        f"    background: {_h('status.success.bg')};"
        f"    border: 1px solid {_h('status.success.border')};"
        "    border-radius: 8px;"
        f"    color: {_h('status.success.deep')};"
        "    font-size: 11px;"
        "}"
        ".scope-title {"
        "    font-size: 11px;"
        "    font-weight: 700;"
        f"    color: {_h('status.success.deep')};"
        "    margin-bottom: 2px;"
        "}"
        ".scope-value {"
        "    margin-top: 4px;"
        "    font-size: 11px;"
        "    font-weight: 700;"
        f"    color: {_h('status.success.deep')};"
        "}"
        ".filter-active-hint {"
        "    margin-bottom: 6px;"
        "    padding: 3px 8px;"
        f"    background: {_r('accent.primary', 0.08)};"
        f"    border: 1px solid {_r('accent.primary', 0.22)};"
        "    border-radius: 6px;"
        f"    color: {_h('accent.primary')};"
        "    font-size: 10px;"
        "    font-weight: 600;"
        "}"
        ".chart-panel {"
        "    margin-bottom: 8px;"
        "    padding: 8px 10px;"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "    border-radius: 8px;"
        "}"
        ".chart-hint {"
        "    margin-top: -4px;"
        "    margin-bottom: 6px;"
        f"    color: {_h('text.muted')};"
        "    font-size: 10px;"
        "}"
        ".chart-hint.subtle {"
        f"    color: {_h('text.muted.quiet')};"
        "    margin-top: -6px;"
        "}"
        ".ring-wrap {"
        "    display: flex;"
        "    align-items: flex-start;"
        "    gap: 10px;"
        "    min-width: 0;"
        "}"
        ".ring-frame {"
        "    width: 176px;"
        "    height: 176px;"
        "    border-radius: 50%;"
        f"    border: 1px solid {_h('separator')};"
        "    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.35);"
        f"    background: {_h('bg.workspace.mid')};"
        "    overflow: hidden;"
        "    flex: 0 0 auto;"
        "}"
        ".ring-image {"
        "    width: 176px;"
        "    height: 176px;"
        "    display: block;"
        "}"
        ".ring-image.ring-fallback {"
        f"    background: radial-gradient(circle at center, {_h('bg.surface.elevated')} 0 28%, {_h('brand.logo.border')} 30% 44%, {_h('bg.workspace.mid')} 46% 100%);"
        "}"
        ".ring-legend {"
        "    display: flex;"
        "    flex-direction: column;"
        "    gap: 4px;"
        "    min-width: 0;"
        "    flex: 1;"
        "}"
        ".ring-item {"
        "    display: flex;"
        "    align-items: flex-start;"
        "    gap: 6px;"
        "    padding: 1px 0;"
        "    border-radius: 6px;"
        "}"
        ".ring-dot-cell {"
        "    width: 12px;"
        "    margin-top: 2px;"
        "    flex: 0 0 auto;"
        "}"
        ".ring-text-cell {"
        "    min-width: 0;"
        "    flex: 1;"
        "}"
        ".ring-value-cell {"
        "    width: 160px;"
        "    margin-left: auto;"
        "    text-align: right;"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "    white-space: nowrap;"
        "    flex: 0 0 auto;"
        "}"
        ".ring-dot {"
        "    width: 9px;"
        "    height: 9px;"
        "    border-radius: 50%;"
        "    display: inline-block;"
        "}"
        ".ring-title {"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    white-space: nowrap;"
        "    overflow: hidden;"
        "    text-overflow: ellipsis;"
        "}"
        ".wf-axis {"
        "    display: flex;"
        "    justify-content: space-between;"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "    margin-bottom: 4px;"
        "    padding: 0 160px 0 160px;"
        "}"
        ".wf-rows {"
        "    display: flex;"
        "    flex-direction: column;"
        "    gap: 4px;"
        "}"
        ".wf-row {"
        "    display: grid;"
        "    grid-template-columns: 150px 1fr 80px 52px;"
        "    gap: 6px;"
        "    align-items: center;"
        "}"
        ".wf-name {"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    white-space: nowrap;"
        "    overflow: hidden;"
        "    text-overflow: ellipsis;"
        "}"
        ".wf-track {"
        "    position: relative;"
        "    height: 12px;"
        "    border-radius: 999px;"
        f"    background: {_h('bg.control')};"
        f"    border: 1px solid {_h('bg.control')};"
        "    overflow: hidden;"
        "}"
        ".wf-seg {"
        "    position: absolute;"
        "    top: 1px;"
        "    bottom: 1px;"
        "    border-radius: 999px;"
        "    opacity: 0.92;"
        "    min-width: 4px;"
        "    transition: filter 100ms ease;"
        "}"
        ".wf-seg:hover {"
        "    filter: brightness(1.08);"
        "}"
        ".wf-add {"
        "    text-align: right;"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    font-weight: 600;"
        "}"
        ".wf-cum {"
        "    text-align: right;"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "    font-weight: 700;"
        "}"
        ".metrics {"
        "    display: flex;"
        "    flex-wrap: wrap;"
        "    gap: 6px;"
        "    margin-bottom: 8px;"
        "}"
        ".metric-card {"
        "    flex: 1;"
        "    min-width: 120px;"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "    border-radius: 8px;"
        "    padding: 8px 10px;"
        "}"
        ".metric-label {"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "    margin-bottom: 3px;"
        "}"
        ".metric-value {"
        "    font-size: 17px;"
        "    font-weight: 700;"
        f"    color: {_h('accent.primary')};"
        "}"
        ".status-panel {"
        "    margin-bottom: 10px;"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "    border-radius: 8px;"
        "    padding: 8px 10px;"
        "}"
        ".mix-track {"
        "    display: flex;"
        "    height: 12px;"
        "    border-radius: 999px;"
        "    overflow: hidden;"
        f"    background: {_h('bg.control')};"
        "}"
        f".mix-seg.success {{ background: {_h('status.success.job')}; }}"
        f".mix-seg.error {{ background: {_h('accent.primary')}; }}"
        f".mix-seg.running {{ background: {_h('status.info')}; }}"
        f".mix-seg.other {{ background: {_h('text.muted.soft')}; }}"
        ".mix-legend {"
        "    margin-top: 6px;"
        "    display: flex;"
        "    flex-wrap: wrap;"
        "    gap: 8px 12px;"
        "}"
        ".mix-item {"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    display: flex;"
        "    align-items: center;"
        "    gap: 6px;"
        "}"
        ".dot {"
        "    width: 8px;"
        "    height: 8px;"
        "    border-radius: 50%;"
        "    display: inline-block;"
        "}"
        f".dot.success {{ background: {_h('status.success.job')}; }}"
        f".dot.error {{ background: {_h('accent.primary')}; }}"
        f".dot.running {{ background: {_h('status.info')}; }}"
        f".dot.other {{ background: {_h('text.muted.soft')}; }}"
        ".row-meta {"
        "    margin-top: 2px;"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "}"
        ".spotlight {"
        "    display: grid;"
        "    grid-template-columns: repeat(3, minmax(180px, 1fr));"
        "    gap: 6px;"
        "    margin-bottom: 8px;"
        "}"
        ".spotlight-card {"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "    border-radius: 8px;"
        "    padding: 7px 9px;"
        "}"
        ".spotlight-card.model {"
        f"    border-color: {_h('status.success.border')};"
        f"    background: {_h('status.success.bg')};"
        "}"
        ".spotlight-rank {"
        "    font-size: 10px;"
        f"    color: {_h('text.muted')};"
        "}"
        ".spotlight-name {"
        "    margin-top: 2px;"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    white-space: nowrap;"
        "    overflow: hidden;"
        "    text-overflow: ellipsis;"
        "}"
        ".spotlight-value {"
        "    margin-top: 4px;"
        "    font-size: 15px;"
        f"    color: {_h('accent.primary')};"
        "    font-weight: 700;"
        "}"
        "table {"
        "    width: 100%;"
        "    border-collapse: collapse;"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "}"
        "th, td {"
        "    padding: 6px 8px;"
        f"    border-bottom: 1px solid {_h('separator')};"
        "    text-align: left;"
        "    font-size: 11px;"
        "}"
        "th {"
        f"    color: {_h('text.secondary')};"
        "    font-weight: 700;"
        f"    background: {_h('bg.workspace')};"
        "}"
        ".bars {"
        "    margin-bottom: 8px;"
        f"    background: {_h('bg.surface.elevated')};"
        f"    border: 1px solid {_h('separator')};"
        "    border-radius: 8px;"
        "    padding: 8px;"
        "}"
        ".bar-row {"
        "    display: flex;"
        "    align-items: center;"
        "    gap: 6px;"
        "    margin: 5px 0;"
        "}"
        ".bar-name {"
        "    width: 240px;"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    overflow: hidden;"
        "    text-overflow: ellipsis;"
        "    white-space: nowrap;"
        "}"
        ".bar-track {"
        "    flex: 1;"
        "    height: 8px;"
        f"    background: {_h('bg.control')};"
        "    border-radius: 4px;"
        "    overflow: hidden;"
        "}"
        ".bar-fill {"
        "    height: 100%;"
        f"    background: linear-gradient(90deg, {_h('accent.light')}, {_h('accent.primary')});"
        "}"
        ".bar-fill.model {"
        f"    background: linear-gradient(90deg, {_h('status.success.job')}, {_h('status.success.deep')});"
        "}"
        ".bar-val {"
        "    width: 80px;"
        "    text-align: right;"
        "    font-size: 11px;"
        f"    color: {_h('text.body')};"
        "    font-weight: 600;"
        "}"
        ".empty {"
        "    padding: 26px 8px;"
        "    text-align: center;"
        f"    color: {_h('text.muted')};"
        "    font-size: 11px;"
        "}"
        "@media (max-width: 900px) {"
        "    .spotlight {"
        "        grid-template-columns: 1fr;"
        "    }"
        "    .bar-name {"
        "        width: 120px;"
        "    }"
        "    .ring-wrap {"
        "        flex-direction: column;"
        "    }"
        "    .wf-axis {"
        "        padding: 0 0 0 0;"
        "    }"
        "    .wf-row {"
        "        grid-template-columns: 110px 1fr 70px 48px;"
        "    }"
        "}"
        )
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{body}</body></html>"
        )

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        from novel_forge.desktop.shutdown_utils import safe_disconnect

        if self._save_btn is not None:
            safe_disconnect(self._save_btn.clicked)
        if self._refresh_btn is not None:
            safe_disconnect(self._refresh_btn.clicked)
        if self._currency_combo is not None:
            safe_disconnect(self._currency_combo.currentIndexChanged)
        if self._exchange_spin is not None:
            safe_disconnect(self._exchange_spin.valueChanged)


__all__ = [
    "TokenAnalyticsTab",
    "collect_project_token_analytics",
    "estimate_token_cost_cny",
    "load_token_dashboard_prefs",
    "save_token_dashboard_prefs",
]
