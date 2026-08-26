export default function RoleSelector({ roles, role, onChange }) {
  return (
    <select className="role-selector" value={role} onChange={(e) => onChange(e.target.value)} title="Investigate as this role (RBAC)">
      {roles.map((r) => (
        <option key={r.role} value={r.role}>
          {r.role.replace(/_/g, ' ')}
        </option>
      ))}
    </select>
  )
}
