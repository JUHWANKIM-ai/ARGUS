import meta from "../public/data/meta.json";

/**
 * 랜딩 — 🔴 **이 파일은 뼈대다.** 다음 파도가 여기에 기업 검색과 관계망 그림을 놓는다.
 * 지금은 골격이 서고 빌드가 도는지만 확인한다. **없는 수를 지어 쓰지 않는다** —
 * 아래 두 수는 전부 `public/data/meta.json` 에서 읽는다.
 */
export default function Home() {
  const { contracts, corrections } = meta.counts;
  const share = Math.round((corrections / contracts) * 10);

  return (
    <main className="page">
      <div className="container">
        <h1 style={{ fontSize: "var(--text-3xl)", maxWidth: "18ch" }}>
          누가 누구에게서 일을 받는가
        </h1>
        <p className="muted" style={{ fontSize: "var(--text-lg)", marginTop: "var(--gap-4)" }}>
          공시 {meta.counts.disclosures.toLocaleString()}건을 관계로 이었습니다.
        </p>

        <p style={{ marginTop: "var(--gap-6)" }}>
          <span className="tag-corrected">정정</span>{" "}
          공시 10건 중 {share}건이 나중에 정정됩니다.{" "}
          <a href="/corrections/" style={{ color: "var(--tone-accent)", fontWeight: 700 }}>
            정정된 공시 보기 →
          </a>
        </p>
      </div>
    </main>
  );
}
