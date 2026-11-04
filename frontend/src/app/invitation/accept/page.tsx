import SchoolAuthForm from "../../../components/SchoolAuthForm";

type InvitationAcceptancePageProps = {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function InvitationAcceptancePage({
  searchParams,
}: InvitationAcceptancePageProps) {
  const params = await searchParams;
  const token = Array.isArray(params.token) ? params.token[0] : params.token;
  return <SchoolAuthForm mode="accept-invitation" token={token} />;
}
