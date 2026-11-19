/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export: `next build` writes `out/`, which replaces `dist/` on PythonAnywhere.
  output: 'export',
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
}

export default nextConfig
