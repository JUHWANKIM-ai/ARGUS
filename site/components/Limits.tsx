/**
 * 🔴 **한계를 화면에 둔다. 숨기면 신뢰를 잃는다.**
 *
 * 다른 공시 서비스와 우리가 다른 점은 "근거 없는 답은 하지 않는다" 는 것이고,
 * 그 말이 진심이려면 **어디까지가 근거인지**를 같은 화면에 적어야 한다.
 * `kinds` 로 그 화면에 해당하는 것만 고른다 — 해당 없는 고지는 잡음이다.
 */
export const LIMIT_TEXT = {
  universe: "70개사만 다룹니다.",
  counterparty: "계약 상대 표기가 공시마다 달라 같은 회사가 여러 이름으로 잡힐 수 있습니다.",
  correction: "정정 공시가 원공시를 가리키지 못하는 경우가 있습니다.",
  network: "공시에 상대가 명시된 계약만 담습니다 — 전체 거래관계가 아닙니다.",
} as const;

export type LimitKind = keyof typeof LIMIT_TEXT;

export default function Limits({ kinds }: { kinds: readonly LimitKind[] }) {
  if (kinds.length === 0) return null;
  return (
    <aside className="limits">
      <h2>이 화면이 못 하는 것</h2>
      <ul>
        {kinds.map((kind) => (
          <li key={kind}>{LIMIT_TEXT[kind]}</li>
        ))}
      </ul>
    </aside>
  );
}
