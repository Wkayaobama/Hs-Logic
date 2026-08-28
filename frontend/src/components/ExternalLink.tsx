interface ExternalLinkProps {
  href: string;
  title?: string;
}

export default function ExternalLink({ href, title }: ExternalLinkProps) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title={title}
      className="text-gray-400 hover:text-blue-600 transition-colors"
    >
      ↗
    </a>
  );
}
