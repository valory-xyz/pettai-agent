const SOURCEMAP_HEAVY_PACKAGES = [
  /node_modules/,
];

const addSourceMapExclusions = (webpackConfig) => {
  const rules = webpackConfig?.module?.rules;
  if (!rules) {
    return;
  }

  rules.forEach((rule) => {
    if (!rule || typeof rule !== 'object') {
      return;
    }

    const isSourceMapLoader =
      typeof rule.loader === 'string' &&
      rule.loader.includes('source-map-loader');

    if (!isSourceMapLoader) {
      return;
    }

    const existingExcludes = Array.isArray(rule.exclude)
      ? rule.exclude
      : rule.exclude
      ? [rule.exclude]
      : [];

    rule.exclude = [...existingExcludes, ...SOURCEMAP_HEAVY_PACKAGES];
  });
};

module.exports = {
  webpack: {
    configure: (config) => {
      addSourceMapExclusions(config);
      return config;
    },
  },
};
