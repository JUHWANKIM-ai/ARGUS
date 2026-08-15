import type { Metadata } from "next";
import "./globals.css";
import Header from "../components/Header";
import meta from "../public/data/meta.json";

export const metadata: Metadata = {
  title: "ARGUS — 공시들 사이의 관계를 읽는다",
  description:
    "공시 하나를 읽는 게 아니라 공시들 사이의 관계를 읽습니다. " +
    "이 공시가 나중에 정정됐는지, 이 회사가 누구와 거래하는지 한 화면에서 봅니다.",
};

/** `2023-01` → `2023.01` — 화면에서는 점을 쓴다. */
function dot(month: string) {
  return month.replace("-", ".");
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <Header />
        {children}
        <footer className="foot">
          <div className="container foot-inner">
            <div>
              <strong>ARGUS</strong> · 공시 {meta.counts.disclosures.toLocaleString()}건을
              관계로 이었습니다
            </div>
            {/*
              🔴 데이터 범위를 발에 늘 띄운다. 70개사뿐이라는 사실을 숨기지 않는 것이
              이 서비스의 정체성이다 — 사람이 화면을 어디까지 믿어도 되는지 알아야 한다.
            */}
            <div className="muted">
              {meta.ranges.companies}개사 · {dot(meta.ranges.from)}~{dot(meta.ranges.to)} ·{" "}
              <a href="/about/">데이터 범위와 한계</a>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
