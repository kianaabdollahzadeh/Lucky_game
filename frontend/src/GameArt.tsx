import type { Box } from "./api";

// The same printed carton is used on the game sleeve and on the playing table.
// Its markings identify the box, never the hidden outcome.
export function MysteryBox({
  number,
  open = false,
}: {
  number: Box;
  open?: boolean;
}) {
  return (
    <svg
      className={`box-art box-art-${number}${open ? " is-open" : ""}`}
      viewBox="0 0 180 210"
      aria-hidden="true"
    >
      <ellipse className="box-shadow" cx="90" cy="187" rx="65" ry="12" />
      <g stroke="var(--ink)" strokeWidth="2.3" strokeLinejoin="round">
        <path className="box-inside" d="m25 91 52-29 79 25-51 34Z" />
        <path className="box-side" d="m105 115 51-28v72l-51 30Z" />
        <path className="box-front" d="m25 91 80 24v74l-80-26Z" />
        <path
          d="m116 137 29-16m-29 24 29-16m-29 24 29-16"
          fill="none"
          stroke="var(--box-print)"
          strokeWidth="3"
        />
        <g transform="matrix(1 .31 0 1 25 91)" stroke="var(--box-print)">
          {number === 1 ? (
            <path
              d="M39 15c-22-20-36 11-17 17-23 9-6 37 17 18 21 20 39-9 17-18 21-10 3-36-17-17Z"
              fill="var(--box-print)"
              strokeWidth="1"
            />
          ) : number === 2 ? (
            <g fill="none" strokeWidth="2.5">
              <circle cx="40" cy="35" r="27" />
              <circle cx="40" cy="35" r="21" />
              <circle cx="40" cy="35" r="15" fill="var(--box-print)" />
            </g>
          ) : (
            <path
              d="m40 6 8 17 20-5-9 18 9 18-20-5-8 17-8-17-20 5 9-18-9-18 20 5Z"
              fill="var(--box-print)"
              strokeWidth="1"
            />
          )}
          <path
            d="M34 27c0-8 14-8 14 0 0 6-7 6-7 12"
            fill="none"
            stroke="var(--box-front)"
            strokeWidth="4"
            strokeLinecap="round"
          />
          <circle
            cx="41"
            cy="47"
            r="2.5"
            fill="var(--box-front)"
            stroke="none"
          />
        </g>
        <g className="box-lid">
          <path className="box-top" d="m20 77 55-31 87 27-56 33Z" />
          <path className="box-front" d="m20 77 86 29v16L20 93Z" />
          <path className="box-side" d="m106 106 56-33v16l-56 33Z" />
          <path d="m53 88 56-31 21 6-56 32v16l-21-7Z" fill="var(--paper)" />
          <path
            d="m64 88 11 4m-7-8 11 4m-7-8 11 4"
            fill="none"
            strokeWidth="1.5"
          />
          <text
            x="0"
            y="0"
            transform="matrix(1 .32 -1 .56 102 70)"
            textAnchor="middle"
            dominantBaseline="central"
            fill="var(--ink)"
            stroke="none"
            fontFamily="DM Sans, sans-serif"
            fontSize="12"
            fontWeight="800"
          >
            0{number}
          </text>
        </g>
      </g>
    </svg>
  );
}

export function ClubMark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`club-mark ${className}`}
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden="true"
    >
      <path d="M24 3 44 14v22L24 47 4 36V14Z" fill="currentColor" />
      <path
        d="m4 14 20 12 20-12M24 26v21"
        stroke="var(--paper)"
        strokeWidth="2"
      />
      <path
        d="M17 15c0-7 14-7 14 0 0 4-7 4-7 7"
        stroke="var(--paper)"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function PlayerToken({
  index,
  className = "",
}: {
  index: number;
  className?: string;
}) {
  return (
    <svg
      className={`player-token token-${index} ${className}`}
      viewBox="0 0 64 64"
      aria-hidden="true"
    >
      {index === 0 ? (
        <path
          d="M32 5C9-9-9 21 10 32-9 45 10 73 32 58 54 73 73 45 54 32 73 12 46-9 32 5Z"
          fill="currentColor"
        />
      ) : index === 1 ? (
        <rect x="4" y="4" width="56" height="56" rx="17" fill="currentColor" />
      ) : index === 2 ? (
        <path
          d="m32 1 9 13 16-3-1 17 8 12-16 6-6 17-13-9-17 3 1-17L1 27l17-7Z"
          fill="currentColor"
        />
      ) : (
        <circle cx="32" cy="32" r="29" fill="currentColor" />
      )}
      <g fill="var(--ink)">
        <ellipse cx="24" cy="27" rx="2.5" ry="4" />
        <ellipse cx="40" cy="27" rx="2.5" ry="4" />
      </g>
      <path
        d={index === 1 ? "M24 39h16" : "M23 37q9 10 18 0"}
        stroke="var(--ink)"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
