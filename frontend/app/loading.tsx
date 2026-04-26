import { Card, CardContent } from "@/components/ui/card";

export default function Loading() {
  return (
    <Card>
      <CardContent className="p-6 text-sm text-muted-foreground">A carregar dados de mercado...</CardContent>
    </Card>
  );
}
