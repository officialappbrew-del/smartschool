import SchoolAuthForm from "../../../components/SchoolAuthForm";

type PasswordResetPageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function PasswordResetPage({
  searchParams,
}: PasswordResetPageProps) {
  const params = await searchParams;
  const token = Array.isArray(params.token) ? params.token[0] : params.token;
  return <SchoolAuthForm mode="reset-password" token={token} />;
}
