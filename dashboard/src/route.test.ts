import { parseRoute } from "./route";

describe("parseRoute", () => {
  it.each([
    ["", "overview"],
    ["#/overview", "overview"],
    ["#/settings", "settings"],
    ["#/settings/extra", "overview"],
    ["#settings", "overview"],
    ["#/<script>", "overview"],
  ])("%j -> %s", (hash, expected) => {
    expect(parseRoute(hash)).toBe(expected);
  });
});
