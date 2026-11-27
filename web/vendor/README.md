# Vendored: @qorechain/qoresign 0.2.0 (Apache-2.0)

`qoresign-verify.js` is an esbuild bundle of `@qorechain/qoresign/network` (with
its `@qorechain/pqc` and `@noble/*` dependencies) so a static page can call
`verifyMessageWithChain` and `verifyMessage` without a bundler at judge time.

Rebuild it with:

    npm install
    npx esbuild --bundle --format=esm --minify --outfile=web/vendor/qoresign-verify.js \
      --alias:@qorechain/qoresign=./node_modules/@qorechain/qoresign <<'JS'
    export { verifyMessageWithChain, verifySiwqWithChain } from "@qorechain/qoresign/network";
    export * from "@qorechain/qoresign";
    JS

The bundle is byte-for-byte what `package.json` pins; `scripts/build_web.py`
does not modify it.
