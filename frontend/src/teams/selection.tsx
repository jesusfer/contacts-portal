export function initialProvider(providers) {
  const query = new URLSearchParams(location.search)
  if (query.has('provider')) {
    const match = providers.find((provider) => String(provider.id) === query.get('provider'))
    if (!match)
      throw new Error(
        'The selected organization is not configured for Teams. Ask your administrator to update this tab.',
      )
    return String(match.id)
  }
  return providers.length === 1 ? String(providers[0].id) : ''
}

export function ProviderSelect({ providers, selected, onChange, disabled = false }) {
  return (
    <label class="teams-provider">
      Organization
      <select
        disabled={disabled}
        value={selected}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        <option value="">Select an organization</option>
        {providers.map((provider) => (
          <option key={provider.id} value={String(provider.id)}>
            {provider.name}
          </option>
        ))}
      </select>
    </label>
  )
}
