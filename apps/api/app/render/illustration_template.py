"""Code-owned GIA and ISA illustration. The worker binds stored outputs; it does not recalculate.

``TEMPLATE_VERSION`` is copied onto the illustration so a stored PDF records which layout produced
it. Opening the document templates library publishes this layout as "GIA and ISA illustration".
The next illustration prints that published version. An already stored PDF is left as it is until
a new render job runs.
"""

TEMPLATE_VERSION = "gia-isa-3"

TEMPLATE_CSS = """
@page {
  size: A4;
  margin: 16mm 16mm 18mm 16mm;
  @bottom-left {
    content: element(illust-footer);
    font-size: 8pt;
    color: #5c564e;
  }
  @bottom-right {
    content: counter(page);
    font-size: 8pt;
    color: #5c564e;
  }
}
@page cover {
  size: A4;
  margin: 0 0 16mm 0;
  @bottom-left {
    content: element(cover-legal);
    font-size: 8pt;
    color: #5c564e;
    margin-left: 16mm;
  }
  @bottom-right { content: none; }
}
body {
  font-family: "Liberation Sans", sans-serif;
  font-size: 10pt;
  color: #1c1917;
  line-height: 1.35;
}
article { color: var(--ink); }
h1 {
  font-family: "Liberation Sans", sans-serif;
  font-size: 28pt;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.05;
  margin: 1mm 0 3mm;
  color: var(--ink);
}
h2 {
  font-family: "Liberation Sans", sans-serif;
  font-size: 12pt;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 7mm 0 2.5mm;
  padding-bottom: 1.2mm;
  border-bottom: 1.25pt solid var(--brand);
  color: var(--ink);
  break-after: avoid;
  page-break-after: avoid;
}
p { margin: 0 0 3mm; }
.keep { break-inside: avoid; page-break-inside: avoid; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 4mm; }
th, td {
  text-align: left;
  padding: 1.5mm 1.8mm;
  border-bottom: 0.3pt solid #e4dfd6;
  vertical-align: top;
}
th {
  font-size: 8pt;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink);
  background: color-mix(in srgb, var(--brand) 16%, white);
  border-bottom: 0.8pt solid var(--brand);
}
table.years th, table.years td { padding: 0.8mm 1.4mm; font-size: 8.5pt; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.note { font-size: 9pt; color: #5c564e; }
.footer { position: running(illust-footer); font-size: 8pt; color: #5c564e; }
.cover { page: cover; page-break-after: always; }
.rule { height: 8mm; background: var(--brand); }
.pad { padding: 12mm 16mm 8mm; }
.eyebrow {
  font-size: 8.5pt;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--brand);
  margin: 0 0 2mm;
}
.wordmark { font-size: 13pt; font-weight: 700; margin: 0 0 8mm; color: var(--ink); }
.logo { height: 14mm; margin: 0 0 6mm; }
.for { font-size: 12pt; margin: 0 0 1mm; }
.meta { color: #5c564e; margin: 0 0 6mm; }
.warning {
  border-left: 2.5pt solid var(--brand);
  background: color-mix(in srgb, var(--brand) 8%, white);
  padding: 3.5mm 4mm;
  font-size: 9pt;
  margin: 0 0 8mm;
}
.lead { font-size: 10.5pt; margin: 0 0 2mm; }
table.rates { width: 100%; border-collapse: separate; border-spacing: 3mm 0; margin: 4mm 0 5mm; }
table.rates td {
  width: 33%;
  background: color-mix(in srgb, var(--brand) 8%, white);
  border: none;
  border-top: 2.5pt solid var(--brand);
  padding: 4.5mm 4mm 5mm;
  vertical-align: top;
}
table.rates td.mid { background: color-mix(in srgb, var(--brand) 18%, white); }
table.rates .k {
  font-size: 8pt;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--brand);
  margin: 0 0 2mm;
}
table.rates .pct {
  font-family: "Liberation Sans", sans-serif;
  font-size: 22pt;
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1;
  margin: 0;
  color: var(--ink);
}
table.rates .sub { font-size: 8pt; color: #5c564e; margin: 1mm 0 3mm; }
table.rates .money { font-size: 14pt; font-weight: 700; margin: 0; }
table.rates .out { font-size: 8pt; color: #5c564e; margin: 1mm 0 0; }
.basis { font-size: 9.5pt; }
.legal { position: running(cover-legal); font-size: 8pt; color: #5c564e; }
"""

TEMPLATE_HTML = """
{% set ink = display.ink_color if display.ink_color is string else 'oklch(0.26 0.055 256)' %}
{% set brand = display.brand_color if display.brand_color is string else 'oklch(0.64 0.115 202)' %}
<article style="--ink: {{ ink }}; --brand: {{ brand }}">
  <section class="cover">
    <div class="rule"></div>
    <div class="pad">
      {% if display.logo is string and display.logo %}
      <img class="logo" src="{{ display.logo }}" alt="">
      {% endif %}
      <p class="wordmark">{{ display.legal_name }}</p>
      <p class="eyebrow">Illustration</p>
      <h1>{{ display.title }}</h1>
      <p class="for">A {{ display.term_years }}-year {{ display.wrapper }} for
      {{ display.client_name }}, starting {{ display.start_date }}.</p>
      <p class="meta">Prepared {{ display.prepared_at }} by {{ display.adviser_firm }}
      · Reference {{ display.reference }}</p>
      <p class="warning">{{ display.warning }}</p>
      <p class="lead">Projected value after {{ display.term_years }} years, in today's money.
      The percentage is the growth rate used for that figure.</p>
      <div class="keep">
      <table class="rates">
        <tr>
          <td>
            <p class="k">Low</p>
            <p class="pct">{{ display.growth_low }}</p>
            <p class="sub">a year, after inflation</p>
            <p class="money">{{ display.projected_low or "Nil" }}</p>
            {% if display.exhausted_low %}<p class="out">Fund runs out
            {{ display.exhausted_low }}</p>{% endif %}
          </td>
          <td class="mid">
            <p class="k">Medium</p>
            <p class="pct">{{ display.growth_mid }}</p>
            <p class="sub">a year, after inflation</p>
            <p class="money">{{ display.projected_mid or "Nil" }}</p>
            {% if display.exhausted_mid %}<p class="out">Fund runs out
            {{ display.exhausted_mid }}</p>{% endif %}
          </td>
          <td>
            <p class="k">High</p>
            <p class="pct">{{ display.growth_high }}</p>
            <p class="sub">a year, after inflation</p>
            <p class="money">{{ display.projected_high or "Nil" }}</p>
            {% if display.exhausted_high %}<p class="out">Fund runs out
            {{ display.exhausted_high }}</p>{% endif %}
          </td>
        </tr>
      </table>
      </div>
      {% if display.target_return is string %}
      <p class="basis">Medium growth is the {{ display.model_name }} target return of
      {{ display.target_return }} a year, less inflation of {{ display.inflation }}.
      {% if display.growth_spread is string %}Low and high sit {{ display.growth_spread }}
      either side of that medium rate. {% endif %}The rates above are the ones this
      illustration uses.</p>
      {% for note in display.warnings %}<p class="note">{{ note }}</p>{% endfor %}
      {% endif %}
      <p class="legal">{{ display.legal_name }} · FCA {{ display.fca }} · Company
      {{ display.company_number }} · {{ display.registered_office }}</p>
    </div>
  </section>

  <div class="footer">
    {{ display.legal_name }}, FCA {{ display.fca }}, company {{ display.company_number }},
    {{ display.registered_office }}.
    {% if display.custodian_name is string and display.custodian_name != "—" %}
    Assets held by {{ display.custodian_name }}{% if display.custodian_fca is string
    and display.custodian_fca != "—" %} (FCA {{ display.custodian_fca }}){% endif %}.
    {% endif %}
  </div>

  <h2>Your plan details</h2>
  <div class="keep">
  <table>
    <tr><th>Client</th><td>{{ display.client_name }}</td></tr>
    <tr><th>Date of birth</th><td>{{ display.dob }}</td></tr>
    {% if display.joint_owner %}
    <tr><th>Joint owner</th><td>{{ display.joint_owner }}</td></tr>
    {% endif %}
    <tr><th>Plan start</th><td>{{ display.start_date }}</td></tr>
    <tr><th>Illustration period</th><td>{{ display.term_years }} years</td></tr>
    <tr><th>Model</th><td>{{ display.model_name }}</td></tr>
    <tr><th>Adviser firm</th><td>{{ display.adviser_firm }}</td></tr>
  </table>
  </div>

  <h2>1. Contributions and withdrawals</h2>
  {% if display.contributions or display.withdrawals %}
  <div class="keep">
  <table>
    <thead><tr><th>From</th><th>To</th><th>Type</th><th class="num">Amount</th><th>
    Frequency</th><th>Escalation</th></tr></thead>
    <tbody>
    {% for row in display.contributions %}
      <tr>
        <td>{{ row.from }}</td><td>{{ row.to }}</td><td>{{ row.kind }}</td>
        <td class="num">{{ row.amount }}</td><td>{{ row.frequency }}</td><td>{{ row.escalation
        }}</td>
      </tr>
    {% endfor %}
    {% for row in display.withdrawals %}
      <tr>
        <td>{{ row.from }}</td><td>{{ row.to }}</td><td>Withdrawal · {{ row.kind }}</td>
        <td class="num">{{ row.amount }}</td><td>{{ row.frequency }}</td><td>{{ row.escalation
        }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% else %}
  <p>No contributions or withdrawals on this illustration.</p>
  {% endif %}

  <h2>2. What the future of the plan could look like</h2>
  <p>Projected value at the end of {{ display.term_years }} years, in today's money, at the
  growth rate shown for each column.</p>
  <div class="keep">
  <table>
    <thead><tr><th></th><th class="num">Low</th><th class="num">Medium</th><th class="num">
    High</th></tr></thead>
    <tbody>
      <tr>
        <td>Growth rate a year</td>
        <td class="num">{{ display.growth_low }}</td>
        <td class="num">{{ display.growth_mid }}</td>
        <td class="num">{{ display.growth_high }}</td>
      </tr>
      <tr>
        <td>Projected value</td>
        <td class="num">{{ display.projected_low or "Nil" }}</td>
        <td class="num">{{ display.projected_mid or "Nil" }}</td>
        <td class="num">{{ display.projected_high or "Nil" }}</td>
      </tr>
      {% if display.exhausted_low or display.exhausted_mid or display.exhausted_high %}
      <tr>
        <td>Fund runs out</td>
        <td class="num">{{ display.exhausted_low or "—" }}</td>
        <td class="num">{{ display.exhausted_mid or "—" }}</td>
        <td class="num">{{ display.exhausted_high or "—" }}</td>
      </tr>
      {% endif %}
    </tbody>
  </table>
  </div>
  <p class="note">These forecasts are not a reliable indicator of future performance.</p>

  <h2>3. How the growth rates are set</h2>
  {% if display.target_return is string %}
  <p>Medium growth is the {{ display.model_name }} target return of {{ display.target_return }}
  a year, less inflation of {{ display.inflation }}.
  {% if display.growth_spread is string %}Low and high sit {{ display.growth_spread }} either
  side of that medium rate. {% endif %}Inflation of {{ display.inflation }} is the allowance
  used to state the result in today's money.</p>
  {% else %}
  <p>{{ display.model_name }}. Real growth {{ display.growth_low }} (low),
  {{ display.growth_mid }} (medium), {{ display.growth_high }} (high). Inflation
  {{ display.inflation }}.</p>
  {% endif %}
  <div class="keep">
  <table>
    <thead><tr><th></th><th class="num">Low</th><th class="num">Medium</th><th class="num">
    High</th></tr></thead>
    <tbody>
      <tr>
        <td>Growth rate applied</td>
        <td class="num">{{ display.growth_low }}</td>
        <td class="num">{{ display.growth_mid }}</td>
        <td class="num">{{ display.growth_high }}</td>
      </tr>
      <tr>
        <td>Inflation allowance</td>
        <td class="num">{{ display.inflation }}</td>
        <td class="num">{{ display.inflation }}</td>
        <td class="num">{{ display.inflation }}</td>
      </tr>
      {% if display.target_return is string %}
      <tr>
        <td>Model target return</td>
        <td class="num">{{ display.target_return }}</td>
        <td class="num">{{ display.target_return }}</td>
        <td class="num">{{ display.target_return }}</td>
      </tr>
      {% endif %}
    </tbody>
  </table>
  </div>
  {% for note in display.warnings %}<p class="note">{{ note }}</p>{% endfor %}

  <h2>4. Summary of charges in the first year</h2>
  <div class="keep">
  <table>
    <thead><tr><th></th><th class="num">Pounds</th><th class="num">Percent of payments in</th>
    </tr></thead>
    <tbody>
      <tr><td>Service charges</td><td class="num">£{{ display.y1_service.pounds }}</td>
      <td class="num">{{ display.y1_service.percent }}%</td></tr>
      <tr><td>Asset charges</td><td class="num">£{{ display.y1_asset.pounds }}</td>
      <td class="num">{{ display.y1_asset.percent }}%</td></tr>
      <tr><td>Total</td><td class="num">£{{ display.y1_total.pounds }}</td><td class="num">
      {{ display.y1_total.percent }}%</td></tr>
    </tbody>
  </table>
  </div>

  <h2>Itemisation of service charges</h2>
  <div class="keep">
  <table>
    <thead><tr><th></th><th class="num">One-off</th><th class="num">Ongoing</th>
    <th class="num">Transaction</th></tr></thead>
    <tbody>
    {% for label, row in display.charge_rows %}
      <tr>
        <td>{{ label }}</td>
        <td class="num">£{{ row.one_off.pounds }}</td>
        <td class="num">£{{ row.ongoing.pounds }}</td>
        <td class="num">£{{ row.transaction.pounds }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>

  <h2>Platform and product charges</h2>
  <p>Tier basis: {{ display.tier_basis }}.{% if display.other_assets %} Other assets on the
  platform used for the tier: {{ display.other_assets }}.{% endif %}</p>
  {% if display.tiers %}
  <div class="keep">
  <table>
    <thead><tr><th>From</th><th>To</th><th class="num">Rate</th></tr></thead>
    <tbody>
    {% for tier in display.tiers %}
      <tr><td>{{ tier.from }}</td><td>{{ tier.to }}</td><td class="num">{{ tier.rate }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  {% endif %}
  {% if display.dfm_rate is string and display.dfm_rate != "0.00%" %}
  <p>Discretionary manager charge {{ display.dfm_rate }}.</p>
  {% endif %}
  {% if display.product_rate is string and display.product_rate != "0.00%" %}
  <p>Product charge {{ display.product_rate }}.</p>
  {% endif %}
  {% if display.cash_paid is string and display.cash_paid != "—" %}
  <p>Cash interest paid to you {{ display.cash_paid }}. Retained cash interest
  {{ display.cash_retained }}.</p>
  {% endif %}

  <h2>Adviser charges</h2>
  <p>Initial {{ display.adviser_initial }}. Ongoing {{ display.adviser_ongoing }}.</p>

  <h2>Asset charges</h2>
  {% if display.assets %}
  <p class="note">Cost data date {{ display.cost_date }}.</p>
  <div class="keep">
  <table>
    <thead>
      <tr><th>Asset</th><th class="num">Allocation</th><th class="num">One-off</th>
      <th class="num">Ongoing</th><th class="num">Incidental</th><th class="num">
      Transaction</th></tr>
    </thead>
    <tbody>
    {% for asset in display.assets %}
      <tr>
        <td>{{ asset.name }}</td>
        <td class="num">{{ asset.allocation }}</td>
        <td class="num">{{ asset.oneoff }}</td>
        <td class="num">{{ asset.ongoing }}</td>
        <td class="num">{{ asset.incidental }}</td>
        <td class="num">{{ asset.transaction }}</td>
      </tr>
    {% endfor %}
      <tr>
        <td>Weighted average</td><td></td>
        <td class="num">{{ display.weighted_oneoff }}</td>
        <td class="num">{{ display.weighted_ongoing }}</td>
        <td class="num">{{ display.weighted_incidental }}</td>
        <td class="num">{{ display.weighted_transaction }}</td>
      </tr>
    </tbody>
  </table>
  </div>
  {% else %}
  <p>Fund costs are not included in this illustration.</p>
  {% endif %}

  <div class="keep">
  <h2>5. Effect of charges on future value</h2>
  <table class="years">
    <thead>
      <tr>
        <th>Year</th><th class="num">Payments in</th><th class="num">Withdrawals</th>
        <th class="num">Before charges</th><th class="num">After product charges</th>
        <th class="num">After all charges</th>
      </tr>
    </thead>
    <tbody>
    {% for row in display.table %}
      <tr>
        <td>{{ row.year }}{% if row.final %} (final){% endif %}</td>
        <td class="num">{{ row.paid_in }}</td>
        <td class="num">{{ row.withdrawn }}</td>
        <td class="num">{{ row.before_charges }}</td>
        <td class="num">{{ row.after_product_charges }}</td>
        <td class="num">{{ row.after_all_charges }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
  <p>If there were no charges the plan could be worth {{ display.before_final }}.
  After all charges it could be worth {{ display.after_all_final }}.</p>
  {% if display.riy_product_text %}<p>{{ display.riy_product_text }}</p>{% endif %}
  {% if display.riy_all_text %}<p>{{ display.riy_all_text }}</p>{% endif %}

  <h2>Other important notes</h2>
  <ul>
    <li>You may get back less than you invest.</li>
    <li>Figures are calculated in full and rounded for display: projection values to the nearest
    pound, first-year charges to the penny, rates to two decimal places.</li>
    {% if display.cash_paid is string and display.cash_paid != "—" %}
    <li>Cash interest paid to you is {{ display.cash_paid }}. The platform retains
    {{ display.cash_retained }}.</li>
    {% endif %}
    {% for note in display.warnings %}<li>{{ note }}</li>{% endfor %}
  </ul>
  <p class="note">Contact your adviser if you have questions about this illustration.</p>
</article>
"""
