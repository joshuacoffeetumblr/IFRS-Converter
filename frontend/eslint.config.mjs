import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * eslint-config-next 16 ships native flat configs, so no FlatCompat shim.
 */
const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    rules: {
      // Architecture §13: accounting arithmetic belongs on the server. The UI
      // formats values it is given; it never computes them. Monetary values
      // arrive as strings and must not be coerced to binary floats.
      "no-restricted-globals": [
        "error",
        { name: "parseFloat", message: "Monetary values are Decimal strings; see architecture §13." },
      ],
      "@typescript-eslint/no-explicit-any": "error",
    },
  },
];

export default config;
