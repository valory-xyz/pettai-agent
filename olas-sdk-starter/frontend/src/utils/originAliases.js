const formatOrigin = (protocol, hostname, port) => {
  const normalizedPort = port ? `:${port}` : '';
  return `${protocol}//${hostname}${normalizedPort}`;
};

export const getOriginAliases = origin => {
  try {
    const parsed = new URL(origin);
    const port = parsed.port;
    const aliases = new Set([origin]);
    aliases.add(formatOrigin(parsed.protocol, parsed.hostname, port));

    if (parsed.hostname === 'localhost') {
      aliases.add(formatOrigin(parsed.protocol, '127.0.0.1', port));
    } else if (parsed.hostname === '127.0.0.1') {
      aliases.add(formatOrigin(parsed.protocol, 'localhost', port));
    }

    return Array.from(aliases);
  } catch (_error) {
    return [origin];
  }
};

export default getOriginAliases;
