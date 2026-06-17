const SOURCEMAP_HEAVY_PACKAGES = [/node_modules/];

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
  babel: {
    loaderOptions: (babelLoaderOptions) => {
      if (Array.isArray(babelLoaderOptions?.plugins)) {
        babelLoaderOptions.plugins = babelLoaderOptions.plugins.map((plugin) => {
          if (Array.isArray(plugin) && plugin[0]?.includes('react-refresh/babel')) {
            return [
              plugin[0],
              {
                ...(plugin[1] || {}),
                skipEnvCheck: true,
              },
            ];
          }
          return plugin;
        });
      }
      return babelLoaderOptions;
    },
  },
  webpack: {
    configure: (config) => {
      addSourceMapExclusions(config);

      // Some transitive deps (web3/crypto libs pulled in via @privy-io) use
      // dynamic `require()`, which makes webpack emit
      // "Critical dependency: the request of a dependency is an expression".
      // It is benign, but CI sets CI=true so react-scripts treats warnings as
      // errors and the build fails. Ignore only this specific warning.
      config.ignoreWarnings = [
        ...(config.ignoreWarnings || []),
        (warning) =>
          typeof warning?.message === 'string' &&
          /Critical dependency: the request of a dependency is an expression/.test(
            warning.message,
          ),
      ];

      return config;
    },
  },
};
