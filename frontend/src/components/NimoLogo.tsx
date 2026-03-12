import NimoIcon from "./NimoIcon";

interface NimoLogoProps {
  iconSize?: number;
  className?: string;
}

/**
 * Combined logo: clownfish icon + "nimo" text with brand gradient
 */
export default function NimoLogo({ iconSize = 24, className = "" }: NimoLogoProps) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <NimoIcon size={iconSize} />
      <span
        className="font-bold text-lg bg-gradient-to-r from-orange-500 to-teal-500 bg-clip-text text-transparent"
        style={{ lineHeight: 1 }}
      >
        nimo
      </span>
    </div>
  );
}
