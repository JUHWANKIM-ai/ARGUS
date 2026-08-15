import type { NextConfig } from "next";

const config: NextConfig = {
  // 🔴 서버 없이 어디든 올린다. 런타임 API·DB·SSR 이 없다 — 페이지가 읽는 것은
  //    public/data/*.json 뿐이고 그것은 빌드 타임에 파이썬이 만든다.
  output: "export",

  // 정적 export 에는 이미지 최적화 서버가 없다.
  images: { unoptimized: true },

  // 🔴 out/ 을 정적 호스트에 그대로 올렸을 때 /corrections 가 /corrections/index.html
  //    로 풀리게 한다. 없으면 호스트에 따라 404 가 난다.
  trailingSlash: true,

  // 🔴 참조 프로젝트(scratch/ARGUS_example)가 Next 16 개발 서버에서 Turbopack panic 을
  //    겪었다. 그래서 `npm run dev` 는 webpack 모드다(package.json). 여기서는 Turbopack 이
  //    저장소 루트를 site/ 밖으로 잡지 않게만 못박는다 — 위쪽에 파이썬 저장소가 있다.
  turbopack: { root: process.cwd() },
};

export default config;
