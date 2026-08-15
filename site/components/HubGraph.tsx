import { clip, layoutHubs, selectHubs, type HubNode } from "../lib/hubLayout";
import { nameKey, shortName } from "../lib/format";

type Props = {
  hubs: HubNode[];
  /** 이름 열쇠 → slug. 상대가 우리 70개사면 그 회사 화면으로 잇는다. */
  slugByName: Record<string, string>;
  minCount?: number;
  minPartners?: number;
  limit?: number;
};

const KIND_FILL: Record<string, string> = {
  public: "var(--tone-public)",
  universe: "var(--tone-universe)",
  overseas: "var(--tone-overseas)",
  other: "var(--tone-other)",
};

/**
 * 🔴 **발주처 → 수주사**를 그린다. 그래프 라이브러리를 쓰지 않는다 —
 * 노드 145 · 쌍 44 에서 힘 기반 시뮬레이션은 구조를 흩어 놓기만 한다.
 * 우리 데이터의 실제 모양은 쌍이 아니라 **하나가 여럿에게 일을 주는 것**이고,
 * 허브 방사형이 그 모양 그대로다.
 *
 * 좌표는 `lib/hubLayout.ts` 의 순수 함수가 낸다 — 겹침은 테스트가 막는다.
 */
export default function HubGraph({ hubs, slugByName, minCount = 5, minPartners = 2, limit = 10 }: Props) {
  const chosen = selectHubs(hubs, { minCount, minPartners, limit });
  const layout = layoutHubs(chosen);

  return (
    <figure className="graph">
      <div className="graph-scroll">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        role="img"
        aria-label="발주처와 수주 기업의 관계망"
        className="graph-svg"
      >
        {layout.hubs.map((placed) => {
          const hub = placed.hub;
          return (
            <g key={hub.canonical}>
              {/* 잇는 선을 먼저 — 원과 글자가 그 위에 온다 */}
              {placed.partners.map((p) => (
                <path
                  key={`${hub.canonical}-${p.name}-line`}
                  className="graph-link"
                  d={`M ${placed.x + placed.r} ${placed.y}
                      C ${(placed.x + p.x) / 2} ${placed.y},
                        ${(placed.x + p.x) / 2} ${p.y},
                        ${p.x - p.r} ${p.y}`}
                />
              ))}

              {/* 발주처 — 원 크기가 계약 건수다 */}
              <circle cx={placed.x} cy={placed.y} r={placed.r} fill={KIND_FILL[hub.kind] ?? KIND_FILL.other} />
              <text className="graph-hub-count" x={placed.x} y={placed.y} textAnchor="middle" dominantBaseline="central">
                {hub.count}
              </text>
              {/* 🔴 두 줄의 기준선 간격이 21 이다 — 15px 이름과 12px 설명이 겹치지 않는 최소치다.
                  처음 그렸을 때 둘이 붙어서 벌렸다. 이름은 11자에서 자른다 —
                  가장 큰 원(반지름 35) 옆에서 11자가 왼쪽 끝에 닿는 한계다. */}
              <text className="graph-hub-name" x={placed.x - placed.r - 12} y={placed.y - 5} textAnchor="end">
                {clip(shortName(hub.name), 11)}
              </text>
              <text className="graph-hub-sub" x={placed.x - placed.r - 12} y={placed.y + 16} textAnchor="end">
                {hub.partners.length}개사에 발주
              </text>

              {/* 수주사 — 우리 70개사면 그 회사 화면으로 간다 */}
              {placed.partners.map((p) => {
                const slug = slugByName[nameKey(p.name)];
                const label = clip(shortName(p.name), 20);
                const dot = (
                  <>
                    <circle className="graph-partner-dot" cx={p.x} cy={p.y} r={p.r} />
                    <text className={slug ? "graph-partner-name is-link" : "graph-partner-name"} x={p.x + p.r + 10} y={p.y} dominantBaseline="central">
                      {label}
                    </text>
                  </>
                );
                return slug ? (
                  <a key={`${hub.canonical}-${p.name}`} href={`/company/${slug}/`} className="graph-partner">
                    <title>{`${shortName(p.name)} 화면으로`}</title>
                    {dot}
                  </a>
                ) : (
                  <g key={`${hub.canonical}-${p.name}`}>{dot}</g>
                );
              })}
            </g>
          );
        })}
      </svg>
      </div>

      <figcaption className="graph-legend">
        <span className="graph-legend-note">
          원 크기 = 그 발주처가 낸 계약 건수 · 이름을 누르면 그 회사 화면으로 갑니다
        </span>
        <span className="graph-legend-keys">
          {(["public", "universe", "overseas", "other"] as const).map((kind) => (
            <span key={kind} className="graph-legend-key">
              <i style={{ background: KIND_FILL[kind] }} />
              {{ public: "공공", universe: "국내기업", overseas: "해외", other: "기타" }[kind]}
            </span>
          ))}
        </span>
      </figcaption>
    </figure>
  );
}
