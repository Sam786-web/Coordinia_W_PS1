export default function PageHeader({ title, description, meta, actions }) {
  return (
    <header className="flex flex-wrap items-start gap-x-4 gap-y-3">
      <div className="min-w-0 flex-1">
        <h1 className="text-2xl font-extrabold tracking-tight text-fg">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-fg-2">{description}</p>}
        {meta && <div className="mt-2.5 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  )
}
