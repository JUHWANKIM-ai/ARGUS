/**
 * 화면에 숫자와 이름을 내놓는 방식. **한 곳에서 정한다** — 표와 그래프와 목록이
 * 같은 금액을 다르게 쓰면 사람이 다른 금액으로 읽는다.
 *
 * 🔴 여기에 계산은 없다. 없는 값을 채우지도 않는다 — `null` 이 들어오면 `null` 이라고 쓴다.
 */

/** 거래상대의 갈래. 관계망과 회사 화면이 같은 말을 쓴다. */
export const KIND_LABEL: Record<string, string> = {
  public: "공공",
  universe: "국내기업",
  overseas: "해외",
  other: "기타",
};

export const KIND_ORDER = ["public", "universe", "overseas", "other"] as const;

/**
 * 원 단위 금액을 사람이 읽는 자리로. 🔴 **어림하지 않는다** —
 * 조 단위까지 쓰되 억 단위를 버리지 않는다("22조" 가 아니라 "22조 7,648억").
 */
export function won(amount: number | null | undefined): string | null {
  if (amount == null || !Number.isFinite(amount)) return null;
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  const JO = 1e12;
  const EOK = 1e8;
  if (abs >= JO) {
    const jo = Math.floor(abs / JO);
    const eok = Math.round((abs - jo * JO) / EOK);
    // 반올림이 1조를 채우면 조 단위로 올린다 — "22조 10,000억" 같은 표기를 막는다.
    if (eok >= 10000) return `${sign}${(jo + 1).toLocaleString()}조`;
    return eok === 0 ? `${sign}${jo.toLocaleString()}조` : `${sign}${jo.toLocaleString()}조 ${eok.toLocaleString()}억`;
  }
  if (abs >= EOK) return `${sign}${Math.round(abs / EOK).toLocaleString()}억`;
  return `${sign}${Math.round(abs).toLocaleString()}원`;
}

/** `20250731` → `2025.07.31`. 형식이 다르면 그대로 돌려준다 — 지어내지 않는다. */
export function ymd(value: string | null | undefined): string {
  if (!value) return "-";
  const digits = value.replace(/\D/g, "");
  if (digits.length !== 8) return value;
  return `${digits.slice(0, 4)}.${digits.slice(4, 6)}.${digits.slice(6, 8)}`;
}

/** `2025-07-24` → `2025.07`. 계약 기간은 일까지 볼 일이 드물다. */
export function ym(value: string | null | undefined): string | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})/.exec(value);
  return m ? `${m[1]}.${m[2]}` : value;
}

/**
 * 회사 표기에서 법인격을 뗀다 — `한국수력원자력(주)` → `한국수력원자력`.
 * 🔴 **표기가 흔들린다는 사실 자체는 화면의 한계 고지에 남긴다.** 여기서 떼는 것은
 * 좁은 자리(그래프 라벨)에 넣기 위해서지, 같은 회사라고 단정하기 위해서가 아니다.
 */
export function shortName(name: string): string {
  return name
    .replace(/\s*\((주|株)\)\s*$/u, "")
    .replace(/\s*㈜\s*$/u, "")
    .replace(/^\s*㈜\s*/u, "")
    .replace(/^\s*\(주\)\s*/u, "")
    .replace(/\s*주식회사\s*$/u, "")
    .replace(/^\s*주식회사\s*/u, "")
    .trim();
}

/** 이름 대조용 열쇠. 표기 흔들림(공백·법인격·대소문자)만 걷어낸다. */
export function nameKey(name: string): string {
  return shortName(name).toLowerCase().replace(/\s+/g, "");
}

/**
 * 회사 이름 → slug. 관계망의 상대 표기가 우리 70개사 중 하나인지 여기서 판정한다.
 * 못 찾으면 `undefined` — 링크를 걸지 않는다.
 */
export function slugIndex(companies: { name: string; slug: string }[]): Record<string, string> {
  const index: Record<string, string> = {};
  for (const c of companies) index[nameKey(c.name)] = c.slug;
  return index;
}

/** 정정 추적이 실패한 사유를 사람 말로. 🔴 모르는 사유는 감추지 않고 그대로 보여 준다. */
export const REASON_LABEL: Record<string, string> = {
  before_corpus: "원공시가 우리 수집 기간(2023.01) 이전입니다",
  no_reference: "공시에 원공시를 가리키는 항목이 없습니다",
  not_found: "가리킨 원공시를 코퍼스에서 찾지 못했습니다",
  ambiguous_targets: "후보가 여럿이라 하나로 확정하지 않았습니다",
};

export function reasonText(reason: string | null | undefined): string {
  if (!reason) return "사유가 기록되지 않았습니다";
  return REASON_LABEL[reason] ?? reason;
}
