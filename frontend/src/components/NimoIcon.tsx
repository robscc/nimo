interface NimoIconProps {
  size?: number;
  className?: string;
}

/**
 * Cute cartoon clownfish SVG icon - nimo mascot
 * Inspired by Nemo from Finding Nemo
 */
export default function NimoIcon({ size = 32, className = "" }: NimoIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Body */}
      <ellipse cx="30" cy="34" rx="22" ry="18" fill="#F97316" stroke="#C2410C" strokeWidth="2" />

      {/* White stripes (clownfish pattern) */}
      {/* Stripe 1 - near head */}
      <path
        d="M18 18C20 22 20 44 18 50"
        stroke="white"
        strokeWidth="5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M18 18C20 22 20 44 18 50"
        stroke="#C2410C"
        strokeWidth="0.8"
        strokeLinecap="round"
        fill="none"
        opacity="0.3"
      />

      {/* Stripe 2 - middle */}
      <path
        d="M32 19C34 24 34 44 32 49"
        stroke="white"
        strokeWidth="5"
        strokeLinecap="round"
        fill="none"
      />
      <path
        d="M32 19C34 24 34 44 32 49"
        stroke="#C2410C"
        strokeWidth="0.8"
        strokeLinecap="round"
        fill="none"
        opacity="0.3"
      />

      {/* Stripe 3 - near tail */}
      <path
        d="M44 23C45 27 45 40 44 44"
        stroke="white"
        strokeWidth="4"
        strokeLinecap="round"
        fill="none"
      />

      {/* Tail fin */}
      <path
        d="M50 28C58 22 60 30 56 34C60 38 58 46 50 40"
        fill="#FB923C"
        stroke="#C2410C"
        strokeWidth="1.5"
      />

      {/* Top fin */}
      <path
        d="M22 18C26 8 36 6 38 16"
        fill="#FB923C"
        stroke="#C2410C"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />

      {/* Bottom fin */}
      <path
        d="M24 50C26 56 30 57 32 51"
        fill="#FB923C"
        stroke="#C2410C"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />

      {/* Eye - white */}
      <circle cx="14" cy="30" r="6" fill="white" stroke="#1E293B" strokeWidth="1.5" />
      {/* Eye - iris */}
      <circle cx="13" cy="30" r="3.5" fill="#1E293B" />
      {/* Eye - pupil highlight */}
      <circle cx="11.5" cy="28.5" r="1.5" fill="white" />

      {/* Friendly smile */}
      <path
        d="M8 38C10 41 14 42 16 40"
        stroke="#C2410C"
        strokeWidth="1.5"
        strokeLinecap="round"
        fill="none"
      />

      {/* Pectoral fin */}
      <path
        d="M20 36C16 40 14 44 18 44C22 44 22 40 20 36Z"
        fill="#FB923C"
        stroke="#C2410C"
        strokeWidth="1"
      />
    </svg>
  );
}
